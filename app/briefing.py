"""Briefing composer — aggregates Hunt's known state into a structured payload
plus a speakable script.

Pure read aggregator. Does NOT mutate any store. Caller passes the singletons
it already has access to (no service-locator anti-pattern).

Data sources, in order of importance for the script:
    profile_store     → user's name + preferred tone (greeting + voice style)
    project_store     → active project + open tasks (the focus)
    action_history    → 3 most recent actions (was Hunt useful lately?)
    conversation_mem  → current chat state (turn count, summary preview)
    mongo_sync        → cloud sync health (one short clause)

Returned shape (BriefingPayload) is JSON-serializable for /briefing endpoint
and for embedding into the /chat/stream `macro_data` event, so the UI can
render a structured card alongside Hunt's spoken briefing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class BriefingPayload:
    """One briefing snapshot. `speakable_script` is what TTS reads aloud;
    every other field is structured data the UI card uses for the visual."""

    greeting: str
    date_human: str
    time_human: str

    active_project: Optional[Dict[str, Any]] = None
    top_open_tasks: List[Dict[str, Any]] = field(default_factory=list)
    open_task_total: int = 0

    recent_actions: List[Dict[str, Any]] = field(default_factory=list)

    session_turns: int = 0
    session_turn_max: int = 10
    session_summary_preview: Optional[str] = None

    cloud_sync: str = "off"  # "on" | "off" | "degraded"

    pending_facts: int = 0  # placeholder for Phase C — always 0 until pending-fact tracking lands

    speakable_script: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BriefingComposer:
    """Stateless composer. Construct once at app startup with refs to the
    stores; call `.compose()` whenever a briefing is requested."""

    MAX_TASKS_IN_SCRIPT = 3
    MAX_TASKS_RETURNED = 6
    MAX_RECENT_ACTIONS = 3
    SUMMARY_PREVIEW_CHARS = 140

    def __init__(
        self,
        profile_store=None,
        project_store=None,
        action_history=None,
        conversation_memory=None,
        mongo_sync=None,
    ):
        self.profile_store = profile_store
        self.project_store = project_store
        self.action_history = action_history
        self.conversation_memory = conversation_memory
        self.mongo_sync = mongo_sync

    # ---------------------------------------------------------------- public

    def compose(self, now: Optional[datetime] = None) -> BriefingPayload:
        now = now or datetime.now()

        name = self._user_name()
        greeting = self._greeting_for(now, name)

        active = self._active_project()
        top_tasks, total = self._open_tasks(active)
        actions = self._recent_actions()
        turn_count, turn_max, summary_preview = self._session_state()
        cloud = self._cloud_sync_status()

        payload = BriefingPayload(
            greeting=greeting,
            date_human=now.strftime("%A, %b %d"),
            time_human=now.strftime("%I:%M %p").lstrip("0"),
            active_project=active,
            top_open_tasks=top_tasks[: self.MAX_TASKS_RETURNED],
            open_task_total=total,
            recent_actions=actions,
            session_turns=turn_count,
            session_turn_max=turn_max,
            session_summary_preview=summary_preview,
            cloud_sync=cloud,
        )
        payload.speakable_script = self._render_script(payload)
        return payload

    # ------------------------------------------------------------- gatherers

    def _user_name(self) -> str:
        if not self.profile_store:
            return ""
        try:
            return (self.profile_store.get().get("name") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _greeting_for(now: datetime, name: str) -> str:
        h = now.hour
        if h < 12:
            tod = "Good morning"
        elif h < 17:
            tod = "Good afternoon"
        elif h < 21:
            tod = "Good evening"
        else:
            tod = "Good night"
        return f"{tod}, {name}." if name else f"{tod}."

    def _active_project(self) -> Optional[Dict[str, Any]]:
        if not self.project_store or not self.conversation_memory:
            return None
        pid = self.conversation_memory.active_project_id
        if not pid:
            return None
        try:
            proj = self.project_store.get(pid)
            if not proj:
                return None
            # Only echo the small set the briefing needs — keeps payload
            # tight and avoids leaking notes into the speakable script.
            return {
                "id": proj.get("id"),
                "name": proj.get("name"),
                "stack": proj.get("stack"),
                "status": proj.get("status"),
            }
        except Exception:
            return None

    def _open_tasks(self, active: Optional[Dict[str, Any]]):
        if not active or not self.project_store:
            return [], 0
        try:
            proj = self.project_store.get(active["id"])
            tasks = [t for t in (proj.get("open_tasks") or []) if not t.get("done")]
            slim = [{"id": t.get("id"), "text": t.get("text")} for t in tasks]
            return slim, len(slim)
        except Exception:
            return [], 0

    def _recent_actions(self) -> List[Dict[str, Any]]:
        if not self.action_history:
            return []
        try:
            entries = self.action_history.list_recent(limit=self.MAX_RECENT_ACTIONS)
        except Exception:
            return []
        out = []
        for e in entries:
            out.append({
                "action": e.get("action"),
                "decision": e.get("decision"),
                "status": e.get("status"),
                "params": e.get("params"),
                "timestamp": e.get("timestamp"),
            })
        return out

    def _session_state(self):
        if not self.conversation_memory:
            return 0, 10, None
        try:
            turns = len(self.conversation_memory.current_session)
            cap = self.conversation_memory.MAX_VERBATIM_EXCHANGES
            summary = (self.conversation_memory.get_running_summary() or "").strip()
            preview = summary[: self.SUMMARY_PREVIEW_CHARS] + (
                "…" if len(summary) > self.SUMMARY_PREVIEW_CHARS else ""
            )
            return turns, cap, preview or None
        except Exception:
            return 0, 10, None

    def _cloud_sync_status(self) -> str:
        if not self.mongo_sync:
            return "off"
        try:
            s = self.mongo_sync.status()
            if not s.get("connected"):
                return "off" if not s.get("configured") else "degraded"
            if s.get("writes_failed", 0) > 0:
                return "degraded"
            return "on"
        except Exception:
            return "off"

    # ---------------------------------------------------- script rendering

    def _render_script(self, p: BriefingPayload) -> str:
        """Build the spoken briefing. Kept under ~45 seconds at normal pace
        (~140 words). Skips empty sections gracefully so a totally empty
        Hunt instance still produces a coherent two-line greeting."""
        parts: List[str] = [p.greeting]

        # Project + open tasks
        if p.active_project:
            name = p.active_project.get("name") or "your project"
            total = p.open_task_total
            if total == 0:
                parts.append(f"You're on {name} with no open tasks right now.")
            elif total == 1:
                t = p.top_open_tasks[0]["text"]
                parts.append(f"On {name}, one task is open: {t}.")
            else:
                top_text = ", ".join(
                    f"{t['text']}" for t in p.top_open_tasks[: self.MAX_TASKS_IN_SCRIPT]
                )
                parts.append(
                    f"On {name}, you have {total} open tasks. Top three: {top_text}."
                )
        else:
            parts.append("No active project right now.")

        # Recent activity
        if p.recent_actions:
            most_recent = p.recent_actions[0]
            label = (most_recent.get("action") or "action").replace("_", " ")
            decision = most_recent.get("decision") or ""
            verb = "completed" if decision == "allow" or decision == "always" else (
                "denied" if decision == "deny" else "ran"
            )
            parts.append(f"Most recent action: {label} {verb}.")

        # Session state — only mention if there's something to say
        if p.session_summary_preview:
            parts.append(f"Earlier in this chat: {p.session_summary_preview}")

        # Cloud sync — brief health line if degraded; silent when fine
        if p.cloud_sync == "degraded":
            parts.append("Cloud sync is degraded; new chats are saved locally only.")
        elif p.cloud_sync == "off" and self.mongo_sync is not None:
            # Configured but off — worth surfacing, otherwise stay silent.
            try:
                if self.mongo_sync.uri:
                    parts.append("Cloud sync is offline.")
            except Exception:
                pass

        return " ".join(parts).strip()
