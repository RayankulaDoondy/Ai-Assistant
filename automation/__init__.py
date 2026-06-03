"""Automation module initialization"""
from .automation_engine import (
    DesktopAutomation,
    BrowserAutomation,
    get_desktop_automation,
    get_browser_automation,
)
from .action_runner import (
    run_action,
    get_action_history,
    ActionHistory,
    ACTION_REGISTRY,
)

__all__ = [
    "DesktopAutomation",
    "BrowserAutomation",
    "get_desktop_automation",
    "get_browser_automation",
    "run_action",
    "get_action_history",
    "ActionHistory",
    "ACTION_REGISTRY",
]
