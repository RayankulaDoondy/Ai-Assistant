"""Persistent per-key approval policies for actions and memory facts.

Shared by:
  - Phase B: Action Queue (key by action_type, e.g. "open_app")
  - Phase C: Memory Approval (key by pattern_name, e.g. "I prefer")

Instantiated with a namespace so the two consumers don't share keys:
    actions_policy = get_approval_store("actions")
    facts_policy   = get_approval_store("fact_patterns")

Policy values: "always", "never", "ask" (default when key is unknown).
"always" → auto-approve; "never" → silently reject; "ask" → show chips.

File: data/approvals/<namespace>.json
Schema:
    {
        "schema": 1,
        "saved_at": "...",
        "policies": { "open_app": "always", "close_app": "ask", ... }
    }
"""
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Dict, Literal, Optional

logger = logging.getLogger(__name__)

Policy = Literal["always", "never", "ask"]
VALID_POLICIES = ("always", "never", "ask")


class ApprovalStore:
    """Namespaced JSON-backed store for {key: policy} maps."""

    SCHEMA_VERSION = 1

    def __init__(self, namespace: str, root_dir: str = "./data/approvals"):
        self.namespace = namespace
        self.path = os.path.join(root_dir, f"{namespace}.json")
        self._policies: Dict[str, Policy] = {}
        self._load()

    # -------- file I/O --------

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            stored = (data or {}).get("policies") or {}
            for k, v in stored.items():
                if isinstance(k, str) and v in VALID_POLICIES:
                    self._policies[k] = v  # type: ignore[assignment]
            logger.info(
                f"Loaded approval policies '{self.namespace}': "
                f"{len(self._policies)} entries from {self.path}"
            )
        except Exception as e:
            logger.warning(f"Could not load approval policies ({self.path}): {e}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            payload = {
                "schema": self.SCHEMA_VERSION,
                "namespace": self.namespace,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "policies": dict(self._policies),
            }
            dir_ = os.path.dirname(self.path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(f"Could not save approval policies ({self.path}): {e}")

    # -------- public API --------

    def get(self, key: str) -> Policy:
        """Return the policy for `key`, defaulting to 'ask'."""
        return self._policies.get(key, "ask")

    def set(self, key: str, policy: Policy) -> Policy:
        """Set the policy for `key`. Returns the new value."""
        if policy not in VALID_POLICIES:
            raise ValueError(f"Invalid policy {policy!r}; expected one of {VALID_POLICIES}")
        self._policies[key] = policy
        self._save()
        return policy

    def remove(self, key: str) -> bool:
        """Remove a key (reverting it to default 'ask'). Returns True if removed."""
        if key in self._policies:
            del self._policies[key]
            self._save()
            return True
        return False

    def all(self) -> Dict[str, Policy]:
        """Return a copy of every stored policy in this namespace."""
        return dict(self._policies)

    def clear(self) -> int:
        """Reset every policy in this namespace. Returns the number dropped."""
        count = len(self._policies)
        self._policies.clear()
        self._save()
        return count


# Per-namespace singletons --------------------------------------------------
_stores: Dict[str, ApprovalStore] = {}


def get_approval_store(namespace: str, root_dir: str = "./data/approvals") -> ApprovalStore:
    """Get or create the ApprovalStore for the given namespace."""
    key = f"{root_dir}:{namespace}"
    if key not in _stores:
        _stores[key] = ApprovalStore(namespace, root_dir=root_dir)
    return _stores[key]
