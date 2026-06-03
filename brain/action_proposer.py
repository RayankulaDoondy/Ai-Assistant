"""Detect actionable user requests and produce an approval proposal.

PROACTIVE design (per user decision): no LLM call. Reads the primary intent
from Reasoning.analyze_intent and extracts parameters with narrow regexes.
Coverage is deliberately small — open_app, close_app, search, open_browser.
If parameter extraction fails, no proposal is emitted and the LLM responds
normally.

A proposal looks like:
    {
        "id": "uuid-hex",
        "action": "open_app",
        "params": {"app_name": "chrome"},
        "prompt": "Open Chrome?",
        "policy": "ask",            # or "always" / "never"
    }
"""
import logging
import re
import uuid
from typing import Dict, List, Optional

from memory import get_approval_store

logger = logging.getLogger(__name__)


# Intents that can correspond to an executable action. Anything else is a
# pure conversation/code/etc. turn and the proposer is a no-op.
ACTION_INTENTS = {"open_app", "close_app", "search", "open_browser"}

# Verbs we recognize per action. Order matters when multiple match.
_OPEN_VERBS = ("open", "launch", "start", "fire up", "boot up")
_CLOSE_VERBS = ("close", "quit", "exit", "shutdown", "shut down", "kill")
_SEARCH_VERBS = ("search for", "search", "find", "look up", "look for", "google",
                 "what is", "who is", "where is")
_BROWSE_VERBS = ("go to", "visit", "navigate to", "open browser to", "browse to")

# Words to strip from the captured target (politeness markers / role hints).
_STRIP_TAIL = re.compile(
    r"\b(please|for me|right now|now|app|application|window)\b.*$",
    re.IGNORECASE,
)


def propose_action(intent: str, message: str) -> Optional[Dict]:
    """Return a proposal dict, or None if no action is implied.

    Caller is responsible for what to do with the proposal: when policy is
    "always" run it silently; "ask" surface the chip; "never" drop it.
    """
    intent_l = (intent or "").lower()
    if intent_l not in ACTION_INTENTS:
        return None

    text = (message or "").strip()
    if not text:
        return None

    if intent_l == "open_app":
        target = _extract_after_verb(text, _OPEN_VERBS)
        if not target:
            return None
        return _build("open_app", {"app_name": target}, f"Open {target}?")

    if intent_l == "close_app":
        target = _extract_after_verb(text, _CLOSE_VERBS)
        if not target:
            return None
        return _build("close_app", {"app_name": target}, f"Close {target}?")

    if intent_l == "search":
        query = _extract_after_verb(text, _SEARCH_VERBS)
        if not query:
            return None
        return _build("search", {"query": query}, f"Search the web for \"{query}\"?")

    if intent_l == "open_browser":
        target = _extract_after_verb(text, _BROWSE_VERBS)
        if not target:
            return None
        return _build("open_browser", {"url": target}, f"Open the browser to {target}?")

    return None


def _build(action: str, params: Dict, prompt: str) -> Dict:
    policy = get_approval_store("actions").get(action)
    return {
        "id": uuid.uuid4().hex,
        "action": action,
        "params": params,
        "prompt": prompt,
        "policy": policy,  # "always" | "never" | "ask"
    }


def _extract_after_verb(text: str, verbs: List[str]) -> Optional[str]:
    """Return the noun phrase immediately after one of `verbs` in `text`.

    Tries longer multi-word verbs first ("search for" before "search") so
    we don't truncate the captured target.
    """
    ordered = sorted(verbs, key=len, reverse=True)
    for verb in ordered:
        # Escape multi-word verbs cleanly.
        verb_re = re.escape(verb).replace(r"\ ", r"\s+")
        pattern = (
            rf"\b{verb_re}\s+"           # the verb + whitespace
            rf"(?:the\s+|my\s+|a\s+)?"   # optional article
            rf"(.+?)"                    # the target (lazy)
            rf"(?:[.!?]|$)"              # stop at end-of-sentence/string
        )
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        raw = m.group(1).strip().strip('"').strip("'")
        # Trim trailing politeness/filler.
        raw = _STRIP_TAIL.sub("", raw).strip()
        if 0 < len(raw) <= 80:
            return raw
    return None
