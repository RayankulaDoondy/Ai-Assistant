"""Response Composer — wraps raw LLM output with personality.

The LLM produces correct answers but they read like a chat box. The composer
adds a brief opening line BEFORE the answer ("Here you go.") and a closing
line AFTER it ("Want me to explain it, optimize it, or…?") so Hunt feels
less like a chatbot and more like a colleague responding.

Design rules
------------
- Intros and outros are STATIC TEMPLATES, not LLM-generated. Zero latency.
- They're randomized per turn so the assistant doesn't feel scripted.
- Code blocks / lists / structured output from the LLM are preserved verbatim.
- Voice mode bypasses the composer entirely — the user just spoke, they want
  the answer back, not "Here you go." spoken at them.
- Errors and macros bypass the composer (they have their own formats).

Public API
----------
    compose_intro(intent, role=None, voice_mode=False) -> str
    compose_outro(intent, response_text, role=None, voice_mode=False) -> str

Both return "" when there's nothing to add (the caller can `if intro:` check).
"""
import random
from typing import List, Optional


# ---------------------------------------------------------------------- #
# Intros — short opening lines, randomized per intent
# ---------------------------------------------------------------------- #
# Trailing "\n\n" is important: the next thing the LLM streams is the actual
# answer, and Markdown rendering needs the blank line to start a new paragraph.
INTROS_TEXT: dict = {
    "code_help": [
        "Here you go.\n\n",
        "Got it — here's the implementation:\n\n",
        "On it.\n\n",
        "Putting that together for you:\n\n",
    ],
    "reasoning": [
        "Let me think through this with you.\n\n",
        "Here's how I'd approach it:\n\n",
        "Walking through the trade-offs:\n\n",
    ],
    "search":          ["Looking that up.\n\n"],
    "task":            ["Got it.\n\n"],
    "open_app":        ["On it.\n\n"],
    "close_app":       ["Got it.\n\n"],
    "file_operation":  ["Got it.\n\n"],
    # Casual chat — intentionally no intro. "Hi" → "Hi back" feels right;
    # "Hi" → "Here you go. Hi back" feels broken.
    "small_talk":     [],
    "conversation":   [],
}


# ---------------------------------------------------------------------- #
# Outros — closing lines that offer next steps
# ---------------------------------------------------------------------- #
OUTROS_CODE: List[str] = [
    "\n\nWant me to **explain how it works**, **optimize it**, **add tests**, or **try a different language**?",
    "\n\nLet me know if you want me to **add comments**, **handle edge cases**, or **show how to call it**.",
    "\n\nHappy to **walk through the logic**, **make it more efficient**, or **add input validation** if useful.",
    "\n\nI can also **convert it to another language**, **add type hints**, or **wrap it in a CLI** if that helps.",
]

OUTROS_REASONING: List[str] = [
    "\n\nWant me to go deeper on any part of this?",
    "\n\nHappy to expand on any point — say which one.",
    "\n\nLet me know if you want me to weigh any of these more carefully.",
]

OUTROS_SEARCH: List[str] = [
    "\n\nWant me to dig deeper into any of these?",
    "\n\nLet me know if you want me to follow up on any specific one.",
]


# ---------------------------------------------------------------------- #
# Heuristics
# ---------------------------------------------------------------------- #
def _looks_like_code(text: str) -> bool:
    """Detect whether the LLM response contains a code block.

    We use this to add a code-style outro even when the intent wasn't tagged
    code_help (e.g. the user asked a conceptual question and Hunt answered
    with an example snippet anyway).
    """
    if not text:
        return False
    return ("```" in text) or (text.count("\n    ") >= 3)


def _is_trivial_reply(text: str) -> bool:
    """Skip the outro for one-line replies — adding 'want me to explain it?'
    after a four-word answer feels off."""
    return len((text or "").strip()) < 60


# ---------------------------------------------------------------------- #
# Public API
# ---------------------------------------------------------------------- #
def compose_intro(intent: Optional[str], role: Optional[str] = None, voice_mode: bool = False) -> str:
    """Pick a short opening line based on intent. Empty string for voice mode
    (voice answers should be tight — no preamble) and for casual chat."""
    if voice_mode:
        return ""
    options = INTROS_TEXT.get((intent or "").lower(), [])
    if not options:
        return ""
    return random.choice(options)


def compose_outro(
    intent: Optional[str],
    response_text: str,
    role: Optional[str] = None,
    voice_mode: bool = False,
) -> str:
    """Pick a closing line that offers next steps.

    Returns "" when:
      - voice_mode is True (don't speak the outro out loud)
      - the reply is trivially short
      - the intent doesn't have an outro template
    """
    if voice_mode:
        return ""
    if _is_trivial_reply(response_text):
        return ""

    intent_lower = (intent or "").lower()

    # The intent-tagged code path takes priority, but we also catch cases where
    # the LLM answered with code even though the intent wasn't code_help.
    if intent_lower == "code_help" or _looks_like_code(response_text):
        return random.choice(OUTROS_CODE)

    if intent_lower == "reasoning":
        return random.choice(OUTROS_REASONING)

    if intent_lower == "search":
        return random.choice(OUTROS_SEARCH)

    return ""


def chunk_text(text: str, chunk_size: int = 12):
    """Yield successive `chunk_size`-character slices of `text`.

    Used by the streaming endpoint to emit intros and outros as a series of
    small token events instead of one large block — that way the typewriter
    cursor in the UI still moves smoothly through composer-added text.
    """
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]
