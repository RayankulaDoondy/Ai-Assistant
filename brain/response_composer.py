"""Response Composer V2 — turn raw LLM output into executive-assistant style.

Philosophy
----------
The old composer added static intro/outro templates around the LLM output.
That's cosmetic. The real personality has to live in the LLM's behavior,
so V2 puts the contract IN THE PROMPT and parses two outputs back:

    1. display_response  — what the user sees on screen (full structure)
    2. voice_response    — what TTS speaks (one-sentence executive summary)

The LLM produces both in a single call, by appending a `[VOICE]:` line at
the very end of its response. The server extracts that line, strips it
from display, and emits a separate `voice` event to the client.

Public API
----------
    classify_depth(intent, message) -> "light" | "medium" | "heavy"
        Decides how much structure to demand from the LLM. Greetings stay
        light (no contract). Substantive asks get the full 4-part structure.

    build_contract(depth, voice_mode) -> str
        Returns the contract block to append to the system prompt context.
        Empty string when the LLM should respond naturally (light queries,
        voice-only sessions).

    extract_voice_and_display(full_text) -> (display, voice)
        Splits the LLM output. If no [VOICE]: marker is found, falls back
        to a heuristic short summary so the speaker never reads code aloud.

Back-compat shims (compose_intro, compose_outro, chunk_text) are kept so
the existing /chat/stream code keeps working while we migrate.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------- #
# Depth classifier
# ---------------------------------------------------------------------- #
# Light queries: greetings, acknowledgements, super-short messages. Forcing
# a 4-part structure here makes Hunt feel patronizing ("Direct answer: hi!").
# Heavy queries: anything where the user clearly wants something built /
# explained / decided. These get the full contract.
# Medium is everything in between.

_LIGHT_INTENTS = {"small_talk", "conversation"}
_HEAVY_INTENTS = {"code_help", "reasoning"}


def classify_depth(intent: Optional[str], message: Optional[str]) -> str:
    """Return one of `light` / `medium` / `heavy` based on how much
    structure the response should carry.

    Rules (in order):
      - small_talk + short message              → light
      - code_help / reasoning intents           → heavy
      - long messages (>180 chars, multi-line)  → heavy
      - asks with multiple "?" or numbered list → heavy
      - everything else                         → medium
    """
    msg = (message or "").strip()
    intent_lc = (intent or "").lower()

    # Light: casual chatter under ~30 chars
    if intent_lc in _LIGHT_INTENTS and len(msg) < 30:
        return "light"

    # Explicit heavy intents
    if intent_lc in _HEAVY_INTENTS:
        return "heavy"

    # Long or multi-part messages → heavy
    if len(msg) > 180 or msg.count("\n") >= 2 or msg.count("?") >= 2:
        return "heavy"

    return "medium"


# ---------------------------------------------------------------------- #
# Response contracts — appended to the system prompt context
# ---------------------------------------------------------------------- #
# We do NOT label the sections in the LLM output. The labels are just a
# scaffold for the model — the prose should flow as if a thoughtful
# colleague wrote it. The [VOICE] line is the only enforced literal.

VOICE_MARKER = "[VOICE]:"


HEAVY_CONTRACT = """\
RESPONSE STRUCTURE FOR THIS REPLY:

Write the response as a thoughtful chief-of-staff would speak to their \
executive. Cover, in this order, WITHOUT using the section labels in your \
output:

1. Direct answer (1-2 sentences). If code is asked for, the answer IS the \
   fenced code block, with one short sentence above it stating what it does.
2. Context (1-2 sentences) — why this approach, what tradeoff to know.
3. Recommendation (1 sentence) — what you would do in their situation.
4. Next actions — 2-4 short bullets the user can pick to move forward \
   (e.g. "explain how it works", "add tests", "convert to TypeScript").

Tone: confident, concrete, never apologetic. First person ("I", "I'd"). \
Never narrate the structure. Never write "as an AI" / "I hope this helps" / \
"feel free to ask". Never read code aloud — describe what you produced.

AT THE VERY END, on its own new line, write exactly:
[VOICE]: <one sentence summary for text-to-speech. Plain prose. No code, \
no markdown, no questions, no bullet syntax. State what you produced and \
what you recommend in human terms.>

The [VOICE] line is REQUIRED. It is stripped from display before showing.
"""

MEDIUM_CONTRACT = """\
RESPONSE GUIDELINES FOR THIS REPLY:

- Lead with the direct answer (1-2 sentences).
- Add ONE sentence of context only if it changes the user's next move.
- End with ONE concise follow-up offer if useful (don't force one).
- Tone: colleague, not chatbot. First person ("I", "I'd"). No "as an AI".

AT THE VERY END, on its own new line, write exactly:
[VOICE]: <one sentence summary for text-to-speech. Plain prose. No markdown.>

The [VOICE] line is REQUIRED. It is stripped from display before showing.
"""

LIGHT_CONTRACT = ""  # No contract — let casual chatter feel casual.


def build_contract(depth: str, voice_mode: bool = False) -> str:
    """Pick the contract block for this turn.

    Returns "" when no contract should be appended:
      - voice_mode is True (the response IS the voice — no dual output needed)
      - depth is "light" (forcing structure on "hi" feels weird)
    """
    if voice_mode:
        return ""
    if depth == "heavy":
        return HEAVY_CONTRACT
    if depth == "medium":
        return MEDIUM_CONTRACT
    return ""


# ---------------------------------------------------------------------- #
# Voice / display split
# ---------------------------------------------------------------------- #
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_MARKDOWN_HEADING_RE = re.compile(r"^#+\s*", re.MULTILINE)
_MARKDOWN_LIST_RE = re.compile(r"^[\-\*\+]\s+|\d+\.\s+", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")


def _strip_for_voice(text: str) -> str:
    """Make text speakable: drop code blocks, markdown, link syntax."""
    if not text:
        return ""
    out = _CODE_BLOCK_RE.sub(" (code is on screen) ", text)
    out = _INLINE_CODE_RE.sub("", out)
    out = _LINK_RE.sub(r"\1", out)
    out = _MARKDOWN_HEADING_RE.sub("", out)
    out = _MARKDOWN_LIST_RE.sub("", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", out)
    out = re.sub(r"\*([^*]+)\*", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _heuristic_voice_summary(display_text: str) -> str:
    """Fallback when the LLM forgot to emit [VOICE]:. Strips code/markdown
    and takes the first speakable sentence, capped at ~200 chars."""
    cleaned = _strip_for_voice(display_text)
    if not cleaned:
        return ""
    # Take up to the first ~200 chars, ending on a sentence boundary if possible.
    if len(cleaned) <= 200:
        return cleaned
    cut = cleaned[:200]
    # Prefer ending at a period within the cut.
    last_period = cut.rfind(". ")
    if last_period > 100:
        return cut[:last_period + 1]
    # Otherwise end at the last space.
    last_space = cut.rfind(" ")
    if last_space > 0:
        return cut[:last_space] + "…"
    return cut + "…"


def extract_voice_and_display(full_text: str) -> Tuple[str, str]:
    """Split the LLM output into (display_text, voice_text).

    Looks for the [VOICE]: marker the contract asks the LLM to emit. The
    marker is matched case-insensitively and we use the LAST occurrence
    (in case the LLM mentioned the marker mid-response by mistake).
    """
    text = (full_text or "").rstrip()
    if not text:
        return "", ""

    # Case-insensitive last-occurrence search.
    lower = text.lower()
    marker_lower = VOICE_MARKER.lower()
    idx = lower.rfind(marker_lower)
    if idx == -1:
        # Heuristic fallback so voice still works if LLM ignores the contract.
        return text, _heuristic_voice_summary(text)

    display = text[:idx].rstrip()
    voice_raw = text[idx + len(VOICE_MARKER):].strip()
    # Only the first line of what follows the marker — discard anything after.
    voice = voice_raw.split("\n", 1)[0].strip().lstrip(":").strip()
    # Defensive: if the LLM still slipped code/markdown into the voice line.
    voice = _strip_for_voice(voice)
    if not voice:
        voice = _heuristic_voice_summary(display)
    return display, voice


# ---------------------------------------------------------------------- #
# Back-compat shims — kept so existing imports don't break.
# ---------------------------------------------------------------------- #
# V1's composer added template intros/outros around the LLM output. V2
# puts that responsibility on the model itself via the contract, so these
# now return empty strings. Removing them later is a separate cleanup.


def compose_intro(
    intent: Optional[str],
    role: Optional[str] = None,
    voice_mode: bool = False,
) -> str:
    """V1 shim — V2 lets the LLM open the response naturally."""
    return ""


def compose_outro(
    intent: Optional[str],
    response_text: str,
    role: Optional[str] = None,
    voice_mode: bool = False,
) -> str:
    """V1 shim — V2 lets the LLM produce its own next-action suggestions."""
    return ""


def chunk_text(text: str, chunk_size: int = 12):
    """Yield successive `chunk_size`-character slices of `text`. Still used
    by the stream for the (now empty) intro/outro chunks. Harmless when
    `text` is empty — yields nothing."""
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]
