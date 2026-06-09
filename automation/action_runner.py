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
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Translate raw Python exceptions from the automation backends into messages
# the user can actually act on. The chip in the UI displays whatever ends up
# in `detail`, so a `ModuleNotFoundError: No module named 'pyautogui'` would
# otherwise leak straight through and read as a bug rather than the (real)
# "Hunt is running in a Linux container that has no display" structural
# constraint. Server-side logging keeps the raw error for debugging.
_FRIENDLY_ACTION_ERRORS: List[Tuple[str, str]] = [
    # Helper-side errors — surface first because in Docker we now dispatch
    # via HUNT_HELPER_URL, and "helper isn't running" is the most likely
    # failure mode the user will hit before they remember to start it.
    ("Connection refused",
     "Desktop helper isn't running on Windows. Open a PowerShell window "
     "in the Hunt project folder and run `run_helper.bat` (or "
     "`python automation\\desktop_helper.py`), then try again."),
    ("Max retries exceeded",
     "Desktop helper is unreachable at host.docker.internal:9100. Confirm "
     "Docker Desktop is running and start the helper with `run_helper.bat`."),
    ("Name or service not known",
     "Desktop helper is unreachable — Docker can't resolve "
     "host.docker.internal. Restart Docker Desktop and try again."),
    ("ConnectionError",
     "Desktop helper isn't running on Windows. Open a PowerShell window "
     "and run `run_helper.bat`, then try again."),
    ("Read timed out",
     "Desktop helper is running but didn't respond in time. Check the "
     "helper window for errors."),

    # In-container backend errors (the fallthrough path when no helper URL
    # is configured — e.g. on Render or local-native installs that haven't
    # set up the helper yet).
    ("pyautogui",
     "Desktop automation isn't available here — Hunt's container can't reach "
     "the host desktop. Start `run_helper.bat` on Windows (recommended) or "
     "run Hunt natively to enable desktop actions."),
    ("pygetwindow",
     "Window management isn't available here — Hunt's container can't see "
     "the host's windows. Start `run_helper.bat` on Windows or run Hunt natively."),
    ("cannot determine display",
     "Desktop automation needs a real display. Start `run_helper.bat` on "
     "Windows (recommended) or run Hunt natively to enable desktop actions."),
    ("$DISPLAY",
     "Desktop automation needs a real display. Start `run_helper.bat` on "
     "Windows (recommended) or run Hunt natively to enable desktop actions."),
]


def _friendly_error(exc: Exception) -> str:
    """Return a user-facing message for an action-execution exception. Maps
    known raw-error substrings (case-insensitive) to actionable explanations;
    falls back to a trimmed raw string for anything unmapped."""
    raw = str(exc)
    lower = raw.lower()
    for needle, message in _FRIENDLY_ACTION_ERRORS:
        if needle.lower() in lower:
            return message
    return raw[:200] if raw else "Action failed without an error message."


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

    # Path A — HTTP dispatch to a Windows-native helper service. Activated
    # by setting HUNT_HELPER_URL (e.g. http://host.docker.internal:9100).
    # This lets Hunt run in Docker (Linux container, no display access) yet
    # still execute real desktop actions on the user's host machine.
    helper_url = (os.environ.get("HUNT_HELPER_URL") or "").strip().rstrip("/")
    if helper_url:
        try:
            import requests
            token = (os.environ.get("HUNT_HELPER_TOKEN") or "").strip()
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            url = f"{helper_url}/{spec['backend']}/{spec['method']}"
            body = {"args": list(spec["args"](params))}
            # 15s — generous enough for first-run `mailto:` / new-app cases where
            # Windows pops a "pick a default app" dialog and `start` blocks.
            resp = requests.post(url, headers=headers, json=body, timeout=15)
            resp.raise_for_status()
            data = resp.json() or {}
            ok = bool(data.get("ok"))
            if ok:
                return {"status": "completed", "detail": ""}
            return {
                "status": "failed",
                "detail": data.get("error") or "Helper reported the action failed.",
            }
        except Exception as e:
            logger.error(f"run_action helper dispatch failed: {e}", exc_info=False)
            return {"status": "failed", "detail": _friendly_error(e)}

    try:
        # Fallthrough path — no helper configured. Lazy-import the in-container
        # automation backends; if pyautogui is missing or there's no display,
        # the friendly-error mapping below translates the raw error.
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
        # Log the raw error server-side for debugging, but show the user the
        # translated friendly version (Docker → no display, etc.).
        logger.error(f"run_action({action}, {params}) failed: {e}", exc_info=False)
        return {"status": "failed", "detail": _friendly_error(e)}


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
