"""Workspace Awareness — what Hunt can see about your current desktop.

Lets Hunt answer questions like:
    "what am I working on right now?"
    "what's in my clipboard?"
    "is VS Code open?"
    "continue my project"

without follow-up questions, by snapshotting:
    - the active (foregrounded) window — title + owning app
    - all visible windows — for "what's open"
    - the clipboard contents — for paste / "read clipboard"
    - the list of interesting running processes — VS Code, Chrome, etc.

Design rules
------------
- READ-ONLY by default. Nothing here writes to the clipboard, focuses a window,
  or moves the mouse. Those become separate explicit-approval actions in
  automation/action_runner.py later.
- Graceful degradation: each capability has its own try/except. If pygetwindow
  fails on Linux/Mac, the snapshot still returns clipboard + processes.
- Privacy: window titles often leak file paths or chat content. The snapshot
  is returned to whoever asks — currently only the local browser via /workspace
  and the macro layer. Never auto-sent to a cloud LLM without user opt-in.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# Lazy imports — workspace tools are platform-specific and may be absent.
# We catch the ImportError so importing this module doesn't blow up on a
# headless Linux container (Render). Each function re-checks availability.
# ---------------------------------------------------------------------- #
try:
    import pygetwindow as _gw
except Exception as _e:
    _gw = None
    logger.info(f"pygetwindow not available ({_e}); window snapshot will be empty")

try:
    import pyperclip as _pyperclip
except Exception as _e:
    _pyperclip = None
    logger.info(f"pyperclip not available ({_e}); clipboard snapshot will be empty")

try:
    import psutil as _psutil
except Exception as _e:
    _psutil = None
    logger.info(f"psutil not available ({_e}); process snapshot will be empty")


# Processes we consider "interesting" for project-context purposes. Anything not
# in this list gets dropped from the snapshot — full process lists are noisy
# and don't help the LLM ground its answer.
INTERESTING_PROCESSES = {
    "code.exe":          "VS Code",
    "code-insiders.exe": "VS Code Insiders",
    "cursor.exe":        "Cursor",
    "chrome.exe":        "Chrome",
    "msedge.exe":        "Edge",
    "firefox.exe":       "Firefox",
    "brave.exe":         "Brave",
    "notepad.exe":       "Notepad",
    "notepad++.exe":     "Notepad++",
    "explorer.exe":      "File Explorer",
    "WindowsTerminal.exe": "Terminal",
    "powershell.exe":    "PowerShell",
    "cmd.exe":           "Command Prompt",
    "pycharm64.exe":     "PyCharm",
    "idea64.exe":        "IntelliJ IDEA",
    "slack.exe":         "Slack",
    "discord.exe":       "Discord",
    "obs64.exe":         "OBS",
    "spotify.exe":       "Spotify",
    "outlook.exe":       "Outlook",
    "thunderbird.exe":   "Thunderbird",
    "figma.exe":         "Figma",
    "ollama.exe":        "Ollama",
}


@dataclass
class WindowInfo:
    title: str
    is_active: bool = False
    is_minimized: bool = False
    width: int = 0
    height: int = 0


@dataclass
class WorkspaceSnapshot:
    """One frozen look at the user's current workspace."""
    active_window: Optional[WindowInfo] = None
    windows: List[WindowInfo] = field(default_factory=list)
    clipboard: str = ""
    clipboard_truncated: bool = False
    processes: List[str] = field(default_factory=list)   # friendly names of interesting apps
    platform_supported: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------- #
# Individual probes
# ---------------------------------------------------------------------- #
def _get_active_and_windows() -> tuple[Optional[WindowInfo], List[WindowInfo]]:
    if _gw is None:
        return None, []
    try:
        active_handle = None
        try:
            active_handle = _gw.getActiveWindow()
        except Exception:
            pass
        all_handles = []
        try:
            all_handles = _gw.getAllWindows() or []
        except Exception:
            all_handles = []

        active_title = (getattr(active_handle, "title", "") or "").strip() if active_handle else ""
        active_info: Optional[WindowInfo] = None
        windows: List[WindowInfo] = []
        for w in all_handles:
            title = (getattr(w, "title", "") or "").strip()
            if not title:
                continue
            # Filter chrome's invisible utility windows + empty titles
            if len(title) < 1:
                continue
            info = WindowInfo(
                title=title,
                is_active=(title == active_title),
                is_minimized=bool(getattr(w, "isMinimized", False)),
                width=int(getattr(w, "width", 0) or 0),
                height=int(getattr(w, "height", 0) or 0),
            )
            if info.is_active:
                active_info = info
            windows.append(info)
        # Deduplicate by title while preserving order
        seen = set()
        windows_unique: List[WindowInfo] = []
        for w in windows:
            if w.title in seen:
                continue
            seen.add(w.title)
            windows_unique.append(w)
        return active_info, windows_unique
    except Exception as e:
        logger.warning(f"window snapshot failed: {e}")
        return None, []


def _get_clipboard(max_chars: int = 4000) -> tuple[str, bool]:
    if _pyperclip is None:
        return "", False
    try:
        text = _pyperclip.paste() or ""
        if len(text) > max_chars:
            return text[:max_chars], True
        return text, False
    except Exception as e:
        logger.warning(f"clipboard read failed: {e}")
        return "", False


def _get_processes() -> List[str]:
    if _psutil is None:
        return []
    try:
        seen = set()
        out: List[str] = []
        for p in _psutil.process_iter(["name"]):
            try:
                name = (p.info.get("name") or "").lower()
            except Exception:
                continue
            friendly = INTERESTING_PROCESSES.get(name)
            if friendly and friendly not in seen:
                out.append(friendly)
                seen.add(friendly)
        return sorted(out)
    except Exception as e:
        logger.warning(f"process snapshot failed: {e}")
        return []


# ---------------------------------------------------------------------- #
# Public API
# ---------------------------------------------------------------------- #
def get_workspace_snapshot(
    include_clipboard: bool = True,
    include_processes: bool = True,
    include_windows: bool = True,
    max_clipboard_chars: int = 4000,
) -> WorkspaceSnapshot:
    """Return one frozen snapshot of the user's desktop right now.

    Each capability can be toggled off (e.g. for a quick "active window only"
    query). Anything not requested isn't even probed.
    """
    snap = WorkspaceSnapshot()
    snap.platform_supported = _gw is not None or _pyperclip is not None or _psutil is not None

    if include_windows:
        snap.active_window, snap.windows = _get_active_and_windows()
    if include_clipboard:
        snap.clipboard, snap.clipboard_truncated = _get_clipboard(max_chars=max_clipboard_chars)
    if include_processes:
        snap.processes = _get_processes()
    return snap


def format_snapshot_markdown(snap: WorkspaceSnapshot, include_clipboard: bool = True) -> str:
    """Render a snapshot as a human-readable markdown block for the chat bubble."""
    lines: List[str] = []

    if not snap.platform_supported:
        return "Workspace awareness isn't available on this system (no desktop tools)."

    if snap.active_window:
        lines.append(f"**Active right now:** {snap.active_window.title}")
    else:
        lines.append("**Active window:** _none detected_")

    if snap.processes:
        lines.append("")
        lines.append("**Apps running:** " + ", ".join(snap.processes))

    if snap.windows:
        other = [w for w in snap.windows if not w.is_active][:8]
        if other:
            lines.append("")
            lines.append("**Other windows:**")
            for w in other:
                lines.append(f"- {w.title}")
            if len(snap.windows) - 1 > len(other):
                lines.append(f"- _… and {len(snap.windows) - 1 - len(other)} more_")

    if include_clipboard and snap.clipboard:
        preview = snap.clipboard.strip()
        if len(preview) > 240:
            preview = preview[:240] + "…"
        suffix = " _(truncated)_" if snap.clipboard_truncated else ""
        lines.append("")
        lines.append(f"**Clipboard{suffix}:**")
        lines.append("```")
        lines.append(preview)
        lines.append("```")
    elif include_clipboard:
        lines.append("")
        lines.append("**Clipboard:** _empty_")

    return "\n".join(lines)


def format_snapshot_speakable(snap: WorkspaceSnapshot) -> str:
    """Same info but as a tight spoken sentence for voice mode."""
    if not snap.platform_supported:
        return "Workspace awareness isn't available here."
    parts: List[str] = []
    if snap.active_window:
        parts.append(f"You're in {snap.active_window.title}")
    if snap.processes:
        parts.append("You also have " + ", ".join(snap.processes[:5]) + " open")
    if not parts:
        return "I can't see what you're working on right now."
    return ". ".join(parts) + "."
