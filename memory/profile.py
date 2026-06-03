"""Structured user profile — the curated truth about who the user is.

Replaces the noisy flat fact list with eight named fields that Hunt injects
into every non-coder system prompt. Backed by a flat JSON file (NOT ChromaDB)
because the profile is small, has atomic PATCH semantics, and isn't a
semantic-search target.

File: data/profile.json
Schema:
    {
        "schema": 1,
        "saved_at": "2026-05-29T12:34:56",
        "profile": {
            "name": "...",
            "occupation": "...",
            "projects": "...",
            "interests": "...",
            "preferred_tone": "...",
            "daily_schedule": "...",
            "frequent_contacts": "...",
            "goals": "..."
        }
    }
"""
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Field order matters: this is the order the formatted block uses, and the
# order the UI renders the form. Keep them aligned.
PROFILE_FIELDS: List[str] = [
    "name",
    "occupation",
    "projects",
    "interests",
    "preferred_tone",
    "daily_schedule",
    "frequent_contacts",
    "goals",
]

# Human-readable labels for the system prompt block and the UI form.
PROFILE_FIELD_LABELS: Dict[str, str] = {
    "name": "Name",
    "occupation": "Occupation",
    "projects": "Projects",
    "interests": "Interests",
    "preferred_tone": "Preferred tone",
    "daily_schedule": "Daily schedule",
    "frequent_contacts": "Frequent contacts",
    "goals": "Goals",
}


class ProfileStore:
    """Atomic JSON-backed store for the eight-field user profile."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str = "./data/profile.json"):
        self.path = path
        self._profile: Dict[str, str] = {f: "" for f in PROFILE_FIELDS}
        self._load()

    # -------- file I/O --------

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            stored = (data or {}).get("profile") or {}
            for f in PROFILE_FIELDS:
                v = stored.get(f, "")
                if isinstance(v, str):
                    self._profile[f] = v.strip()
            logger.info(
                f"Loaded user profile from {self.path}: "
                f"{sum(1 for v in self._profile.values() if v)}/{len(PROFILE_FIELDS)} fields set"
            )
        except Exception as e:
            logger.warning(f"Could not load profile ({self.path}): {e}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            payload = {
                "schema": self.SCHEMA_VERSION,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "profile": dict(self._profile),
            }
            dir_ = os.path.dirname(self.path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(f"Could not save profile ({self.path}): {e}")
        # Phase D: best-effort Mongo upsert. Local file remains authoritative.
        try:
            from .mongo_sync import mongo_sync_singleton
            m = mongo_sync_singleton()
            if m and m.available:
                m.upsert_profile(self._profile)
        except Exception as e:
            logger.warning(f"Mongo profile sync failed (non-fatal): {e}")

    # -------- public API --------

    def get(self) -> Dict[str, str]:
        """Return a copy of the current profile (all 8 fields, possibly empty)."""
        return dict(self._profile)

    def replace(self, data: Dict[str, str]) -> Dict[str, str]:
        """Replace the entire profile (POST). Unknown fields ignored; missing
        fields cleared to empty string."""
        new = {}
        for f in PROFILE_FIELDS:
            v = (data or {}).get(f, "")
            new[f] = v.strip() if isinstance(v, str) else ""
        self._profile = new
        self._save()
        return self.get()

    def patch(self, data: Dict[str, str]) -> Dict[str, str]:
        """Sparse update (PATCH). Only fields present in `data` are changed."""
        if not isinstance(data, dict):
            return self.get()
        changed = False
        for f, v in data.items():
            if f not in PROFILE_FIELDS:
                continue
            cleaned = v.strip() if isinstance(v, str) else ""
            if self._profile.get(f, "") != cleaned:
                self._profile[f] = cleaned
                changed = True
        if changed:
            self._save()
        return self.get()

    def clear(self) -> Dict[str, str]:
        """Reset every field to empty (DELETE)."""
        self._profile = {f: "" for f in PROFILE_FIELDS}
        self._save()
        return self.get()

    def is_empty(self) -> bool:
        return not any(v for v in self._profile.values())

    def format_for_prompt(self) -> str:
        """Render the non-empty fields as a clean system-prompt block.

        Empty fields are omitted so we never inject 'Name: ' to the model.
        Returns an empty string when nothing is set (caller skips injection).
        """
        if self.is_empty():
            return ""
        lines = ["User profile (curated facts; use these naturally):"]
        for f in PROFILE_FIELDS:
            v = self._profile.get(f, "")
            if v:
                lines.append(f"- {PROFILE_FIELD_LABELS[f]}: {v}")
        return "\n".join(lines)


# Global instance ------------------------------------------------------------
_profile_store: Optional[ProfileStore] = None


def get_profile_store(path: Optional[str] = None) -> ProfileStore:
    """Get or create the singleton ProfileStore."""
    global _profile_store
    if _profile_store is None:
        _profile_store = ProfileStore(path or "./data/profile.json")
    return _profile_store
