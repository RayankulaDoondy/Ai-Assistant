"""Hunt desktop helper — Windows-native HTTP service that executes the
desktop / browser action requests Hunt's Docker container can't run by itself.

Why this exists
---------------
Hunt is running in a Linux container (for stability — Python 3.13 + Windows
asyncio deadlocks otherwise). A Linux container has no path to the Windows
host's display, so pyautogui and similar libs can't drive your real desktop
from inside Docker. This script is the missing native half: it runs on
Windows alongside Docker, exposes a tiny localhost HTTP API, and Hunt's
`action_runner.run_action()` POSTs into it whenever the user approves an
action chip.

Run it
------
    Double-click run_helper.bat   (preferred)
        — OR —
    python automation\\desktop_helper.py

Then test:
    curl http://127.0.0.1:9100/health   →   {"ok": true}

Endpoints
---------
    GET  /health                       — liveness probe (returns {"ok": true})
    POST /desktop/open_application     — body {"args": ["chrome"]}
    POST /desktop/close_application    — body {"args": ["chrome"]}
    POST /browser/open_browser         — body {"args": ["https://example.com"]}
    POST /browser/search               — body {"args": ["python tutorial"]}

Auth
----
    The server binds 127.0.0.1 only — only processes on this machine can
    reach it. If HUNT_HELPER_TOKEN env var is set, every request must
    include `Authorization: Bearer <token>`.

Stopping
--------
    Ctrl+C in the console, or close the window.

Adding more actions
-------------------
    1. Implement a Python function with the same name as the action's method
       (mirror automation/automation_engine.py).
    2. Register the (backend, method) → function pair in REGISTRY.
    3. Also add the (action, backend, method) entry to ACTION_REGISTRY in
       automation/action_runner.py so the chip flow knows it exists.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

# Wake-word listener is optional (deps may not be installed). Import it
# lazily so the helper still runs without openwakeword + sounddevice.
try:
    from wake_listener import WakeListener  # type: ignore
except ImportError:
    try:
        # When run from project root (python automation/desktop_helper.py),
        # the sibling import path differs.
        from automation.wake_listener import WakeListener  # type: ignore
    except ImportError:
        WakeListener = None  # type: ignore


# Where Hunt's API is reachable from this helper. Helper runs natively on
# the host; Hunt's Docker container exposes 8001 → 8001. Override via
# env var if Hunt runs on a non-default port or on a remote machine.
HUNT_URL = (os.environ.get("HUNT_URL") or "http://localhost:8001").rstrip("/")

# Wake-word state — populated in main() if deps are available.
_wake_listener: Optional["WakeListener"] = None


# --------------------------------------------------------------------- #
# Action implementations — local to this Windows process
# --------------------------------------------------------------------- #

# Friendly aliases users say in chat → Windows executables we can launch.
# Anything not in this map is tried as-is (so "outlook" works without an
# entry if outlook.exe is on PATH).
_APP_ALIASES: Dict[str, str] = {
    "chrome":     "chrome",
    "edge":       "msedge",
    "firefox":    "firefox",
    "brave":      "brave",
    "notepad":    "notepad",
    "calculator": "calc",
    "calc":       "calc",
    "explorer":   "explorer",
    "files":      "explorer",
    "settings":   "ms-settings:",
    "vs code":    "code",
    "vscode":     "code",
    "code":       "code",
    "terminal":   "wt",
    "powershell": "powershell",
    "cmd":        "cmd",
    "word":       "winword",
    "excel":      "excel",
    "outlook":    "outlook",
    "teams":      "teams",
    "slack":      "slack",
    "discord":    "discord",
    "spotify":    "spotify",
    # Email aliases — `mailto:` opens whatever the user has set as their
    # default email client (Outlook, Thunderbird, the browser-based mailto
    # handler, etc.). If you'd rather these open Gmail in the browser,
    # change the value to "https://mail.google.com".
    "email":             "mailto:",
    "mail":              "mailto:",
    "email app":         "mailto:",
    "mail app":          "mailto:",
    "email application": "mailto:",
    "mail application":  "mailto:",
    "gmail":             "https://mail.google.com",
    "inbox":             "mailto:",
}


def _resolve_app(name: str) -> str:
    """Map common user phrasing ('chrome') to the executable name we'll
    feed to Windows' `start` command. Falls back to the raw name so the
    user can say arbitrary exe names."""
    if not name:
        return ""
    key = name.strip().lower()
    return _APP_ALIASES.get(key, key)


def open_application(name: str) -> bool:
    """Launch a Windows application by name. Uses cmd's `start` builtin so
    Path resolution + protocol handlers (e.g. ms-settings:) both work.

    Returns True on apparent success (start command exited cleanly). False
    if the name is empty or start raised."""
    target = _resolve_app(name)
    if not target:
        return False
    try:
        # `start ""` — the empty string is a placeholder window title that
        # cmd's `start` requires before the actual command when there are
        # quotes / spaces in the target.
        subprocess.run(
            f'start "" {target}',
            shell=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        logging.info(f"open_application: launched {target!r}")
        return True
    except Exception as e:
        logging.error(f"open_application({name!r}) failed: {e}")
        return False


def close_application(name: str) -> bool:
    """Close a Windows application via taskkill. Tries the resolved alias
    first, then with .exe appended if the user said something like 'chrome'."""
    target = _resolve_app(name)
    if not target:
        return False
    # taskkill needs the image name (with or without .exe). Try both forms.
    candidates: List[str] = []
    if target.endswith(".exe"):
        candidates.append(target)
    else:
        candidates.append(f"{target}.exe")
        candidates.append(target)

    last_err = ""
    for img in candidates:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", img],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                logging.info(f"close_application: killed {img}")
                return True
            last_err = (result.stderr or result.stdout or "").strip()
        except Exception as e:
            last_err = str(e)
    logging.error(f"close_application({name!r}) failed for all candidates: {last_err}")
    return False


def open_browser(url: str) -> bool:
    """Open a URL in the user's default browser."""
    if not url:
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        # Treat bare domains as https.
        url = "https://" + url
    try:
        webbrowser.open(url)
        logging.info(f"open_browser: opened {url}")
        return True
    except Exception as e:
        logging.error(f"open_browser({url!r}) failed: {e}")
        return False


def search(query: str, engine: str = "google") -> bool:
    """Open a Google search for the query in the default browser."""
    if not query:
        return False
    base = {
        "google":     "https://www.google.com/search?q=",
        "duckduckgo": "https://duckduckgo.com/?q=",
        "bing":       "https://www.bing.com/search?q=",
    }.get((engine or "google").lower(), "https://www.google.com/search?q=")
    return open_browser(base + quote_plus(query))


# --------------------------------------------------------------------- #
# Registry maps URL path (backend, method) → executor function. The
# action_runner side uses spec["backend"] / spec["method"] to build the
# URL, so the names here must match those in automation/action_runner.py
# ACTION_REGISTRY.
# --------------------------------------------------------------------- #

REGISTRY: Dict[Tuple[str, str], Callable[..., bool]] = {
    ("desktop", "open_application"):  open_application,
    ("desktop", "close_application"): close_application,
    ("browser", "open_browser"):      open_browser,
    ("browser", "search"):            search,
}


# --------------------------------------------------------------------- #
# Wake-word detection — when the listener fires, POST to Hunt so it can
# fan the event out to the UI via SSE. UI then starts the mic flow as
# if the user had tapped the orb.
# --------------------------------------------------------------------- #

def _post_wake_to_hunt(wake_word: str, confidence: float) -> None:
    """Callback invoked from the wake-listener thread on each detection."""
    try:
        import requests
        url = f"{HUNT_URL}/voice/wakeword"
        # Short timeout — Hunt's handler is fast (queue.put_nowait); if it's
        # not responding, we don't want to back up the audio thread.
        requests.post(
            url,
            json={"wake_word": wake_word, "confidence": confidence},
            timeout=3,
        )
        logging.info(f"Wake event POSTed to Hunt ({wake_word} {confidence:.2f})")
    except Exception as e:
        # Hunt may be restarting or unreachable — non-fatal, we just lose
        # this one detection. The next will retry.
        logging.warning(f"Could not POST wake event to Hunt: {e}")


# --------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------- #

TOKEN = (os.environ.get("HUNT_HELPER_TOKEN") or "").strip()


class _Handler(BaseHTTPRequestHandler):
    server_version = "HuntDesktopHelper/1.0"

    def _json(self, status: int, payload: Dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client gave up — nothing to do, don't crash the server.

    def _check_auth(self) -> bool:
        if not TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {TOKEN}"

    # ---- GET /health, /wake/status ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {
                "ok": True,
                "auth_required": bool(TOKEN),
                "wake_available": WakeListener is not None,
                "wake_running": bool(_wake_listener and _wake_listener.is_running()),
            })
            return
        if path == "/wake/status":
            if _wake_listener is None:
                self._json(200, {
                    "available": False,
                    "reason": "wake-word libs not installed (see WAKEWORD_SETUP.md)",
                })
                return
            self._json(200, {"available": True, **_wake_listener.status()})
            return
        self._json(404, {"ok": False, "error": "not found"})

    # ---- POST /<backend>/<method>  or  /wake/{start,stop} ----
    def do_POST(self):
        if not self._check_auth():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return

        parts = urlparse(self.path).path.strip("/").split("/")
        # Wake-word control endpoints — single-segment under /wake/.
        if len(parts) == 2 and parts[0] == "wake":
            if _wake_listener is None:
                self._json(400, {
                    "ok": False,
                    "error": "wake-word libs not installed (see WAKEWORD_SETUP.md)",
                })
                return
            if parts[1] == "start":
                ok = _wake_listener.start()
                self._json(200, {"ok": ok, **_wake_listener.status()})
                return
            if parts[1] == "stop":
                _wake_listener.stop()
                self._json(200, {"ok": True, **_wake_listener.status()})
                return
            self._json(404, {"ok": False, "error": f"unknown wake action: {parts[1]}"})
            return

        if len(parts) != 2:
            self._json(400, {"ok": False, "error": "expected /<backend>/<method>"})
            return
        backend, method = parts

        fn = REGISTRY.get((backend, method))
        if fn is None:
            self._json(404, {"ok": False, "error": f"unknown action: {backend}/{method}"})
            return

        # Parse JSON body — {"args": [...]} (or empty for no args).
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._json(400, {"ok": False, "error": f"bad JSON body: {e}"})
            return

        args = payload.get("args") or []
        if not isinstance(args, list):
            self._json(400, {"ok": False, "error": "`args` must be a list"})
            return

        try:
            ok = bool(fn(*args))
        except TypeError as e:
            self._json(400, {"ok": False, "error": f"argument mismatch: {e}"})
            return
        except Exception as e:
            logging.exception(f"executor crashed for {backend}/{method}")
            self._json(500, {"ok": False, "error": str(e)})
            return

        self._json(200, {"ok": ok})

    # Silence the default per-request stderr noise; we log explicitly above.
    def log_message(self, format, *args):
        return


def main():
    global _wake_listener
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    port = int(os.environ.get("HUNT_HELPER_PORT", "9100"))
    addr = ("127.0.0.1", port)

    try:
        server = HTTPServer(addr, _Handler)
    except OSError as e:
        logging.error(f"Could not bind to {addr[0]}:{addr[1]}: {e}")
        logging.error("Is another helper already running? Or is port 9100 in use?")
        sys.exit(1)

    logging.info(f"Hunt desktop helper listening on http://{addr[0]}:{port}")
    if TOKEN:
        logging.info("Auth: HUNT_HELPER_TOKEN is set; requests must carry Bearer header.")
    else:
        logging.info("Auth: localhost-only (no token configured).")

    # Try to start the wake-word listener. Failure (missing deps, missing
    # mic, model load error) is logged but does NOT abort the helper —
    # users running without wake-word still get the action chip features.
    if WakeListener is not None:
        wake_model = os.environ.get("HUNT_WAKE_MODEL", "hey_jarvis_v0.1")
        _wake_listener = WakeListener(
            on_wake=_post_wake_to_hunt,
            model_name=wake_model,
        )
        # Auto-start unless explicitly disabled.
        if os.environ.get("HUNT_WAKE_AUTOSTART", "1") != "0":
            started = _wake_listener.start()
            if started:
                logging.info(f"Wake word: listening for '{wake_model}' (POSTs to {HUNT_URL}/voice/wakeword)")
            else:
                logging.warning(f"Wake word: not started — {_wake_listener.status().get('error')}")
        else:
            logging.info("Wake word: autostart disabled (set HUNT_WAKE_AUTOSTART=1 to enable)")
    else:
        logging.info(
            "Wake word: disabled (install deps via: "
            "pip install openwakeword sounddevice numpy)"
        )

    logging.info("Press Ctrl+C to stop. Hunt's action chips work while this stays open.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down.")
        if _wake_listener is not None:
            _wake_listener.stop()
        server.server_close()


if __name__ == "__main__":
    main()
