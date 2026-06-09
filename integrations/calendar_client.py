"""Thin wrapper around Google's Calendar API client.

Used by the `calendar_search` tool the LLM calls when Hunt is in Research
Mode and the user asks about events / meetings / schedule. Returns compact
event dicts in the same shape Gmail/memory citations use, so the existing
citation-card pipeline picks them up without a special case.

Public surface
--------------
    CalendarClient()                              - loads token + builds service
    .available()                                  - bool, are we connected?
    .search(query, time_min, time_max, max_results) - list of event dicts
    .list_today(max_results)                      - convenience: today's events
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CalendarClient:
    """Wraps Google Calendar API + Hunt's stored OAuth credentials.

    Construct once per tool call. If `available()` returns False, the user
    hasn't connected Calendar yet — the tool surfaces a clean
    "calendar_not_connected" so the LLM can ask the user to authorize.
    """

    def __init__(self):
        self._service = None
        self._user_email: Optional[str] = None
        self._error: Optional[str] = None
        self._build()

    def _build(self) -> None:
        try:
            from integrations import calendar_auth
        except Exception as e:
            self._error = f"Calendar auth module unavailable: {e}"
            return
        creds = calendar_auth.load_credentials()
        if not creds:
            self._error = "Calendar not connected. The user needs to authorize via the settings drawer."
            return
        self._user_email = calendar_auth.connected_email()
        try:
            from googleapiclient.discovery import build
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        except Exception as e:
            self._error = f"Could not build Calendar service: {e}"

    def available(self) -> bool:
        return self._service is not None

    def error(self) -> Optional[str]:
        return self._error

    def user_email(self) -> Optional[str]:
        return self._user_email

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #

    def search(
        self,
        query: Optional[str] = None,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
    ) -> Dict[str, Any]:
        """Search the user's primary calendar.

        Args:
            query: optional free-text query (matches summary/description/location/attendees).
                   Omit for "all events in the window".
            time_min: RFC3339 timestamp (e.g. '2026-06-08T00:00:00Z'). Defaults to now.
            time_max: RFC3339 timestamp. Defaults to time_min + 7 days when omitted.
            max_results: cap on returned events (default 10, max 50).
        """
        if not self.available():
            return {"error": self._error or "Calendar unavailable", "results": [], "count": 0}

        # Default window: now → 7 days from now. The LLM can override via args.
        if not time_min:
            time_min = datetime.now(timezone.utc).isoformat()
        if not time_max:
            try:
                tm = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
            except Exception:
                tm = datetime.now(timezone.utc)
            time_max = (tm + timedelta(days=7)).isoformat()

        max_results = max(1, min(50, int(max_results) if max_results else 10))

        params: Dict[str, Any] = {
            "calendarId": "primary",
            "timeMin": _to_rfc3339(time_min),
            "timeMax": _to_rfc3339(time_max),
            "maxResults": max_results,
            "singleEvents": True,    # expand recurring → one event per instance
            "orderBy": "startTime",
        }
        if query and query.strip():
            params["q"] = query.strip()

        try:
            resp = self._service.events().list(**params).execute()
        except Exception as e:
            return {"error": f"Calendar search failed: {e}", "results": [], "count": 0}

        items = resp.get("items") or []
        results: List[Dict[str, Any]] = [_compact_event(ev) for ev in items]
        return {
            "query": query or "",
            "time_min": params["timeMin"],
            "time_max": params["timeMax"],
            "results": results,
            "count": len(results),
            "user_email": self._user_email,
        }

    # ----------------------------------------------------------------- #
    # Convenience — today's events
    # ----------------------------------------------------------------- #

    def list_today(self, max_results: int = 20) -> Dict[str, Any]:
        """Return everything on the primary calendar between local midnight
        today and local midnight tomorrow. Honors the system's local timezone
        (the server's tzinfo), which for Hunt-on-Docker maps to UTC unless
        TZ env var is set."""
        now = datetime.now(timezone.utc).astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return self.search(
            query=None,
            time_min=start.isoformat(),
            time_max=end.isoformat(),
            max_results=max_results,
        )


# --------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------- #

def _to_rfc3339(value: str) -> str:
    """Google Calendar wants RFC3339 (e.g. '2026-06-08T00:00:00Z' or with
    '+05:30' offset). Accept anything fromisoformat can parse; pass through
    if already a string we trust. Falls back to the raw input on parse
    failure so the API call surfaces a real error instead of us silently
    losing the user's intent."""
    if not value:
        return ""
    try:
        # Normalize 'Z' → '+00:00' for fromisoformat.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Naive timestamp — assume UTC. Calendar requires a tz suffix.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return value


def _compact_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the fields the LLM and citation card actually need."""
    start = (ev.get("start") or {})
    end = (ev.get("end") or {})
    # All-day events use 'date' (yyyy-mm-dd); timed events use 'dateTime'.
    start_str = start.get("dateTime") or start.get("date") or ""
    end_str = end.get("dateTime") or end.get("date") or ""

    attendees = ev.get("attendees") or []
    attendee_summary = ", ".join(
        a.get("email") or a.get("displayName") or ""
        for a in attendees[:5]
        if (a.get("email") or a.get("displayName"))
    )

    return {
        "id": ev.get("id"),
        "summary": ev.get("summary") or "(no title)",
        "description": (ev.get("description") or "")[:600],
        "location": ev.get("location") or "",
        "status": ev.get("status") or "",
        "html_link": ev.get("htmlLink") or "",
        "start": start_str,
        "end": end_str,
        "all_day": "date" in start and "dateTime" not in start,
        "organizer": (ev.get("organizer") or {}).get("email") or "",
        "attendees": attendee_summary,
        "attendee_count": len(attendees),
        "meet_link": _extract_meet_link(ev),
    }


def _extract_meet_link(ev: Dict[str, Any]) -> str:
    """Find a Google Meet (or other conferencing) link if attached."""
    conf = ev.get("conferenceData") or {}
    for entry in conf.get("entryPoints") or []:
        if entry.get("entryPointType") == "video":
            return entry.get("uri") or ""
    return ev.get("hangoutLink") or ""
