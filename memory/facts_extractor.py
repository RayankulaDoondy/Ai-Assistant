"""Extract durable profile-fact CANDIDATES from chat messages.

Phase C change: this no longer auto-stores. It returns structured candidate
dicts so the caller (main.py) can surface approval chips per the user-chosen
"approval chips only" memory policy.

Each candidate carries:
  - text:           the formatted fact (what gets stored if user clicks Save)
  - pattern:        the policy key used by ApprovalStore("fact_patterns")
  - captured:       the raw extracted value (used for profile promotion)
  - profile_field:  the structured profile field this fact most naturally
                    fits, or None — used to pre-select the promote dropdown
"""
import logging
import re
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Each entry: short policy key, regex, template, and the structured profile
# field this candidate would naturally promote to (or None).
# Order matters when overlapping patterns match — earlier wins via the seen set.
_PATTERNS = [
    {
        "key": "remember",
        "re": re.compile(r"\bremember(?:\s+that|\s+to|,)?\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User wants me to remember: {}",
        "profile_field": None,
    },
    {
        "key": "name",
        # The verbs are case-insensitive (inline (?i:...)) but the captured name
        # part is case-sensitive: must start uppercase, and any additional
        # word in the capture must also start uppercase. That way
        # "my name is Rayan and I prefer..." captures only "Rayan", not the
        # whole sentence tail.
        "re": re.compile(
            r"\b(?i:my name is|call me|i'?m called)\s+([A-Z][a-zA-Z\-']{0,30}(?:\s+[A-Z][a-zA-Z\-']{0,30})*)"
        ),
        "template": "User's name is {}",
        "profile_field": "name",
    },
    {
        "key": "prefer",
        "re": re.compile(r"\bi(?:'?d)?\s+prefer\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User prefers: {}",
        "profile_field": "preferred_tone",
    },
    {
        "key": "like",
        "re": re.compile(r"\bi\s+(?:like|love|enjoy)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User likes: {}",
        "profile_field": "interests",
    },
    {
        "key": "dislike",
        "re": re.compile(r"\bi\s+(?:don'?t\s+like|dislike|hate)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User dislikes: {}",
        "profile_field": None,  # not a clean profile field
    },
    {
        "key": "occupation_work",
        "re": re.compile(r"\bi\s+work\s+(?:as|at)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User works as/at: {}",
        "profile_field": "occupation",
    },
    {
        "key": "occupation_im",
        "re": re.compile(r"\bi'?m\s+(?:a|an)\s+([\w\s\-]{2,40}?)(?:[.!,?]|$)", re.IGNORECASE),
        "template": "User is a/an: {}",
        "profile_field": "occupation",
    },
    {
        "key": "location",
        "re": re.compile(r"\bi(?:'?m\s+based|\s+live|'?m\s+from)\s+(?:in|at|from)?\s*(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User location: {}",
        "profile_field": None,
    },
    {
        "key": "preference_explicit",
        "re": re.compile(r"\bsave\s+(?:this|that|it)\s+as\s+(?:a\s+)?(?:preference|note)\b[:\s]*(.+?)(?:[.!?]|$)", re.IGNORECASE),
        "template": "User preference: {}",
        "profile_field": None,
    },
]


# Stop words / phrases that disqualify a match — protects against catching
# rhetorical use of "I'm a" inside hypothetical or question phrasings.
_DISQUALIFIERS = (
    "?",
    "would i", "could i", "should i", "if i", "what if",
    "imagine", "pretend",
)


def extract_facts(message: str) -> List[Dict]:
    """Return candidate-fact dicts (NOT stored — caller decides via chips).

    Returns an empty list when no durable fact is detected.
    """
    if not message or not message.strip():
        return []

    lowered = message.lower()
    if any(token in lowered for token in _DISQUALIFIERS):
        return []

    candidates: List[Dict] = []
    seen: set = set()
    for pat in _PATTERNS:
        for match in pat["re"].finditer(message):
            captured = (match.group(1) or "").strip().strip("'\"`")
            if len(captured) < 2 or len(captured) > 160:
                continue
            text = pat["template"].format(captured)
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "id": uuid.uuid4().hex,
                "text": text,
                "pattern": pat["key"],
                "captured": captured,
                "profile_field": pat.get("profile_field"),
            })

    if candidates:
        logger.info(f"Extracted {len(candidates)} fact candidate(s)")
    return candidates


def pattern_keys() -> List[str]:
    """Return all pattern keys (for the settings UI listing policies)."""
    return [p["key"] for p in _PATTERNS]
