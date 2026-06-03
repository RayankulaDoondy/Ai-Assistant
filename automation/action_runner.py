"""Execute approved actions and keep a small history for the UI.

Wraps the existing DesktopAutomation / BrowserAutomation engines behind a
single `run_action(action, params)` dispatcher so /chat doesn't need to know
which automation backend handles which action.

History is a ring buffer of the last 50 entries (executed, denied, failed)
persisted atomically to data/actions_history.json so it survives restarts.
"""
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Each action's dispatcher key, friendly label, and required-params list. The
# dispatcher is resolved at call time so we don't crash on startup when an
# automation backend is missing.
ACTION_REGISTRY: Dict[str, Dict] = {
    "open_app": {
        "label": "Open app",
        "required": ("app_name",),
        "backend": "desktop",
        "method": "open_application",
        "args": lambda params: (params.get("app_name", ""),),
    },
    "close_app": {
        "label": "Close app",
        "required": ("app_name",),
        "backend": "desktop",
        "method": "close_application",
        "args": lambda params: (params.get("app_name", ""),),
    },
    "search": {
        "label": "Web search",
        "required": ("query",),
        "backend": "browser",
        "method": "search",
        "args": lambda params: (params.get("query", ""),),
    },
    "open_browser": {
        "label": "Open URL",
        "required": ("url",),
        "backend": "browser",
        "method": "open_browser",
        "args": lambda params: (params.get("url", ""),),
    },
}


def run_action(action: str, params: Dict) -> Dict:
    """Dispatch an action to the appropriate automation backend.

    Returns a dict: {"status": "completed"|"failed", "detail": str}.
    Catches every exception so the chat endpoint never crashes on a bad action.
    """
    spec = ACTION_REGISTRY.get(action)
    if not spec:
        return {"status": "failed", "detail": f"Unknown action: {action}"}

    for key in spec["required"]:
        if not (params.get(key) or "").strip():
            return {"status": "failed", "detail": f"Missing parameter: {key}"}

    try:
        # Lazy import keeps the brain module independent of the (heavy)
        # automation backends, and means a missing PyAutoGUI doesn't break
        # the rest of Hunt — only actions of that type fail gracefully.
        if spec["backend"] == "desktop":
            from automation import get_desktop_automation
            backend = get_desktop_automation()
        else:
            from automation import get_browser_automation
            backend = get_browser_automation()
        method = getattr(backend, spec["method"])
        ok = bool(method(*spec["args"](params)))
        if ok:
            return {"status": "completed", "detail": ""}
        return {"status": "failed", "detail": f"{spec['method']} returned False"}
    except Exception as e:
        logger.error(f"run_action({action}, {params}) failed: {e}", exc_info=False)
        return {"status": "failed", "detail": str(e)}


class ActionHistory:
    """Bounded JSON-backed history of recent action decisions."""

    MAX_ENTRIES = 50

    def __init__(self, path: str = "./data/actions_history.json"):
        self.path = path
        self.entries: List[Dict] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("entries") if isinstance(data, dict) else None
            if isinstance(entries, list):
                self.entries = [e for e in entries if isinstance(e, dict)]
                self.entries = self.entries[: self.MAX_ENTRIES]
        except Exception as e:
            logger.warning(f"Could not load action history: {e}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            payload = {"schema": 1, "entries": self.entries}
            dir_ = os.path.dirname(self.path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(f"Could not save action history: {e}")

    def record(
        self,
        action: str,
        params: Dict,
        decision: str,
        result: Optional[Dict] = None,
    ) -> Dict:
        entry = {
            "id": uuid.uuid4().hex,
            "action": action,
            "params": params,
            "decision": decision,  # "allow" | "deny" | "always"
            "status": (result or {}).get("status", "n/a"),
            "detail": (result or {}).get("detail", ""),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.entries.insert(0, entry)
        self.entries = self.entries[: self.MAX_ENTRIES]
        self._save()
        # Phase D: best-effort Mongo append.
        try:
            from memory import mongo_sync_singleton
            m = mongo_sync_singleton()
            if m and m.available:
                m.add_action(entry)
        except Exception as e:
            logger.warning(f"Mongo action sync failed (non-fatal): {e}")
        return entry

    def list_recent(self, limit: int = 20) -> List[Dict]:
        return self.entries[:limit]

    def clear(self) -> int:
        count = len(self.entries)
        self.entries.clear()
        self._save()
        return count


_history: Optional[ActionHistory] = None


def get_action_history(path: Optional[str] = None) -> ActionHistory:
    global _history
    if _history is None:
        _history = ActionHistory(path or "./data/actions_history.json")
    return _history
