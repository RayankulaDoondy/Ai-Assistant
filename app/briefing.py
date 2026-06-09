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

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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

    # Phase D additions — proactive data from Gmail + Calendar so Hunt can
    # greet you with "your next meeting is in 42 minutes" instead of asking
    # what's on your mind.
    next_event: Optional[Dict[str, Any]] = None
    inbox_summary: Optional[Dict[str, Any]] = None

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
        # Proactive data — these are HTTP-bound (Gmail + Calendar APIs)
        # so they may add 300-800ms to the compose call when connected.
        # Both fail-open: missing connection or transient API error → None.
        next_event = self._next_event(now)
        inbox = self._inbox_summary()

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
            next_event=next_event,
            inbox_summary=inbox,
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

    # --- Phase D — proactive data gatherers ---------------------------------
    # Both are lazy: they import the integration client at call time so an
    # uninstalled/missing google-api-client doesn't break startup, AND they
    # fail-open (return None on any error) so a degraded Gmail or Calendar
    # never breaks the rest of the briefing.

    def _next_event(self, now: datetime) -> Optional[Dict[str, Any]]:
        """Return the next event on the user's calendar within the next 24h,
        annotated with `minutes_until_start` so the UI can render
        'in 42 minutes' without re-parsing the timestamp client-side."""
        try:
            from integrations.calendar_client import CalendarClient
        except Exception:
            return None
        try:
            from datetime import timedelta, timezone
            client = CalendarClient()
            if not client.available():
                return None
            now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
            result = client.search(
                time_min=now_utc.isoformat(),
                time_max=(now_utc + timedelta(hours=24)).isoformat(),
                max_results=1,
            )
            if result.get("count", 0) == 0:
                return None
            ev = (result.get("results") or [None])[0]
            if not ev:
                return None
            return {
                "summary": ev.get("summary"),
                "start": ev.get("start"),
                "end": ev.get("end"),
                "location": ev.get("location") or "",
                "all_day": bool(ev.get("all_day")),
                "minutes_until_start": self._minutes_until(ev.get("start"), now_utc),
                "meet_link": ev.get("meet_link") or "",
            }
        except Exception:
            return None

    def _inbox_summary(self) -> Optional[Dict[str, Any]]:
        """Return Gmail inbox at-a-glance: total unread + how many are marked
        important + the most-recent unread subject line. Two API calls; both
        fail-open to None so an offline Gmail doesn't break the briefing."""
        try:
            from integrations.gmail_client import GmailClient
        except Exception:
            return None
        try:
            client = GmailClient()
            if not client.available():
                return None
            unread = client.search("is:unread in:inbox", max_results=10)
            if unread.get("error"):
                return None
            unread_count = unread.get("count", 0)
            # Best-effort important count — if it errors, just skip the field.
            important_count = 0
            try:
                important = client.search("is:unread is:important", max_results=10)
                if not important.get("error"):
                    important_count = important.get("count", 0)
            except Exception:
                pass
            top_subject = ""
            top_from = ""
            results = unread.get("results") or []
            if results:
                top_subject = results[0].get("subject") or ""
                top_from = (results[0].get("from") or "").split("<", 1)[0].strip()
            return {
                "unread_count": unread_count,
                "important_count": important_count,
                "top_subject": top_subject,
                "top_from": top_from,
            }
        except Exception:
            return None

    # --- Phase D — Chief of Staff synthesis layer --------------------------
    # Takes the structured facts from compose() and asks an LLM to pick the
    # top 1-3 actionable priorities. Strictly grounded: the prompt tells the
    # model to use ONLY what's in the facts (no invented agenda items, no
    # speculative "you should learn X" hallucination). Empty facts → empty
    # output, never a fabricated "stay focused on your goals" platitude.

    def compose_recommendations(
        self,
        payload: BriefingPayload,
        llm_engine,
        max_priorities: int = 3,
    ) -> "RecommendationsPayload":
        """Synthesize top priorities from the briefing facts. `llm_engine` is
        the global LLMEngine — passed in (not pulled via singleton) so this
        stays testable. Failure modes are non-fatal: an LLM error returns an
        empty list, not an exception."""
        # Strip noisy fields before serializing — the model doesn't need to
        # see internal ids or session-state padding.
        facts = {
            "active_project":   payload.active_project,
            "open_task_total":  payload.open_task_total,
            "top_open_tasks":   [t.get("text") for t in payload.top_open_tasks if t.get("text")],
            "next_event":       payload.next_event,
            "inbox_summary":    payload.inbox_summary,
            "recent_actions":   payload.recent_actions[:2] if payload.recent_actions else [],
        }
        # Quick exit: if there's nothing concrete to prioritize, don't burn
        # an LLM call to produce "consider organizing your day."
        if not (
            facts["active_project"]
            or facts["open_task_total"]
            or facts["next_event"]
            or (facts["inbox_summary"] and facts["inbox_summary"].get("unread_count"))
        ):
            return RecommendationsPayload(priorities=[], reasoning_note="no facts to prioritize")

        prompt = (
            "You are Hunt's Chief of Staff agent. Below is a JSON snapshot "
            "of the user's current state. Pick up to "
            f"{max_priorities} priorities for what they should focus on RIGHT NOW.\n\n"
            "STRICT RULES:\n"
            "1. Ground every priority in a specific fact from the snapshot. "
            "If you can't point to a fact, DON'T include the priority.\n"
            "2. Be specific. 'Reply to the bank statement email from Jio' beats "
            "'check your email'.\n"
            "3. Order by urgency: time-sensitive (meetings starting soon) > "
            "important inbox > project tasks > recent unfinished work.\n"
            "4. Never invent. No motivational filler. No 'consider' or 'maybe'.\n"
            "5. If there's genuinely nothing pressing, return an EMPTY priorities array.\n\n"
            "Output EXACTLY this JSON shape, no markdown, no prose:\n"
            "{\"priorities\": [\n"
            "  {\"rank\": 1, \"title\": \"...\", \"why\": \"...\", \"action\": \"...\"}\n"
            "]}\n\n"
            f"FACTS:\n{json.dumps(facts, indent=2, default=str)}\n\n"
            "Your JSON:"
        )

        try:
            # Small, fast generation. Low temperature for grounded output.
            # No history, no context — the prompt is fully self-contained so
            # we don't waste tokens on session memory the model doesn't need.
            raw = llm_engine.generate(
                prompt=prompt,
                context=None,
                history=None,
                voice_mode=False,
                temperature=0.2,
                response_length="short",
            )
        except Exception as e:
            logger.warning(f"Recommendations LLM call failed: {e}")
            return RecommendationsPayload(priorities=[], reasoning_note=f"llm error: {e}")

        priorities = _parse_recommendations(raw, max_priorities)
        return RecommendationsPayload(priorities=priorities)

    @staticmethod
    def _minutes_until(iso_start: Optional[str], now_utc: datetime) -> Optional[int]:
        """Parse an ISO start time and return whole minutes from now. Returns
        None on parse failure; negative values mean 'already started'."""
        if not iso_start:
            return None
        try:
            start = datetime.fromisoformat(iso_start.replace("Z", "+00:00"))
            from datetime import timezone
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            delta = (start - now_utc).total_seconds()
            return int(delta // 60)
        except Exception:
            return None

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

        # Calendar — next event in the day, with minutes-until phrasing.
        if p.next_event:
            mins = p.next_event.get("minutes_until_start")
            title = p.next_event.get("summary") or "an event"
            if mins is None:
                parts.append(f"Next on your calendar: {title}.")
            elif mins < 0:
                parts.append(f"{title} is happening now.")
            elif mins < 60:
                parts.append(f"Your next meeting, {title}, is in {mins} minutes.")
            else:
                hours = mins // 60
                rem = mins % 60
                if rem == 0:
                    parts.append(f"Your next meeting, {title}, is in {hours} hour{'s' if hours != 1 else ''}.")
                else:
                    parts.append(f"Your next meeting, {title}, is in {hours} hour{'s' if hours != 1 else ''} {rem} minutes.")

        # Inbox — only mention if there's something unread.
        if p.inbox_summary and p.inbox_summary.get("unread_count", 0) > 0:
            unread = p.inbox_summary["unread_count"]
            important = p.inbox_summary.get("important_count", 0)
            if important > 0:
                parts.append(f"You have {unread} unread emails, {important} marked important.")
            else:
                parts.append(f"You have {unread} unread emails.")

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


# ====================================================================== #
# Recommendation payload + parser — used by compose_recommendations()
# ====================================================================== #


@dataclass
class Priority:
    """One item in the Chief of Staff priority list."""
    rank: int
    title: str
    why: str = ""
    action: str = ""


@dataclass
class RecommendationsPayload:
    """Synthesized output of the Chief of Staff agent. `priorities` is
    ranked top-to-bottom; `reasoning_note` is a non-user-facing breadcrumb
    explaining why the list is what it is (LLM error, no facts, etc.)."""
    priorities: List[Priority] = field(default_factory=list)
    reasoning_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "priorities":     [asdict(p) for p in self.priorities],
            "reasoning_note": self.reasoning_note,
        }


def _parse_recommendations(raw: str, max_priorities: int) -> List[Priority]:
    """Extract a JSON priority list from the LLM's raw output.

    Defensive: strips markdown code fences if the model wrapped its JSON
    in ```json ... ```; if no valid JSON is found, returns an empty list
    rather than crashing. The model usually obeys the strict-shape prompt
    but smaller routed models occasionally wander.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences.
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    # Find the first { ... } block — covers the case where the model
    # prepended a sentence like "Here's the JSON:".
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        text = brace_match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Recommendations parse failed ({e}): {raw[:200]!r}")
        return []

    items = data.get("priorities") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    out: List[Priority] = []
    for i, raw_item in enumerate(items[:max_priorities]):
        if not isinstance(raw_item, dict):
            continue
        title = str(raw_item.get("title") or "").strip()
        if not title:
            continue
        try:
            rank = int(raw_item.get("rank") or (i + 1))
        except (TypeError, ValueError):
            rank = i + 1
        out.append(Priority(
            rank=rank,
            title=title[:140],
            why=str(raw_item.get("why") or "").strip()[:200],
            action=str(raw_item.get("action") or "").strip()[:160],
        ))
    return out
