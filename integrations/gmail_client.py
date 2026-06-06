"""Thin wrapper around Google's Gmail API client.

Used by the `gmail_search` / `gmail_read` / `gmail_attachment` tools that
the LLM can call when Hunt is in Research Mode. Keeps the Google-specific
shapes contained here so the tools can return Hunt-friendly dicts.

Design notes
------------
- One client instance per request is fine — google-api-python-client builds
  a lightweight discovery-document-backed object; the heavy lifting is in
  the OAuth credentials which we cache to disk.
- We do NOT decode attachment payloads here — the LLM rarely needs full
  attachment bytes, just metadata. The dedicated `gmail_attachment` tool
  pulls + decodes when asked.
- Gmail's API uses base64url encoding for message bodies; we decode to
  utf-8 with errors='replace' so weird charsets don't crash the tool.
- All public methods return `{"error": "..."}` on failure rather than
  raising — the LLM sees errors as tool results and can recover.

Public surface
--------------
    GmailClient()                          - constructs (loads token)
    .available()                            - bool, are we connected?
    .search(query, max_results=10)          - list of compact dicts
    .get_message(message_id)                - full message dict
    .get_attachment(message_id, attach_id)  - decoded bytes + filename
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GmailClient:
    """Wraps the Gmail API client + Hunt's stored OAuth credentials.

    Construct once per tool call. If `available()` returns False, the
    user hasn't connected Gmail yet — the tool should surface a clean
    "Gmail not connected, ask the user to authorize" message instead of
    crashing.
    """

    def __init__(self):
        self._service = None
        self._user_email: Optional[str] = None
        self._error: Optional[str] = None
        self._build()

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #

    def _build(self) -> None:
        """Pull credentials from disk and stand up a Gmail service handle.

        Doesn't raise — sets self._error instead, so callers can branch
        on .available() and produce a friendly tool result.
        """
        try:
            from integrations import gmail_auth
        except Exception as e:
            self._error = f"Gmail auth module unavailable: {e}"
            return
        creds = gmail_auth.load_credentials()
        if not creds:
            self._error = "Gmail not connected. The user needs to visit /auth/google/start to authorize."
            return
        self._user_email = gmail_auth.connected_email()
        try:
            from googleapiclient.discovery import build
            # cache_discovery=False avoids a noisy file-not-found warning when
            # the lib tries to cache the discovery doc to disk in containers
            # where that path doesn't exist.
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        except Exception as e:
            self._error = f"Could not build Gmail service: {e}"

    # ----------------------------------------------------------------- #
    # Status
    # ----------------------------------------------------------------- #

    def available(self) -> bool:
        return self._service is not None

    def error(self) -> Optional[str]:
        return self._error

    def user_email(self) -> Optional[str]:
        return self._user_email

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #

    def search(self, query: str, max_results: int = 10) -> Dict[str, Any]:
        """Run a Gmail search and return compact per-message metadata.

        `query` accepts Gmail's native search syntax — full power of the
        Gmail search bar: `from:bank@example.com after:2024/05/01 has:attachment`
        Cap `max_results` at 50 so the LLM can't accidentally fetch
        thousands of mails.
        """
        if not self.available():
            return {"error": self._error or "Gmail unavailable", "results": [], "count": 0}
        max_results = max(1, min(50, int(max_results) if max_results else 10))
        try:
            resp = (
                self._service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
        except Exception as e:
            return {"error": f"Gmail search failed: {e}", "results": [], "count": 0}

        message_stubs = resp.get("messages") or []
        results: List[Dict[str, Any]] = []
        for stub in message_stubs:
            mid = stub.get("id")
            if not mid:
                continue
            # Fetch just enough metadata for an at-a-glance citation. The
            # `metadata` format avoids pulling the full body until the
            # caller actually wants it via get_message().
            try:
                msg = (
                    self._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["Subject", "From", "Date", "To"],
                    )
                    .execute()
                )
            except Exception as e:
                logger.info(f"Gmail get(metadata) failed for {mid}: {e}")
                continue
            results.append(_compact_message(msg))
        return {
            "query": query,
            "results": results,
            "count": len(results),
            "user_email": self._user_email,
        }

    # ----------------------------------------------------------------- #
    # Read single message
    # ----------------------------------------------------------------- #

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """Return the full message: headers, plain-text body, attachment manifest."""
        if not self.available():
            return {"error": self._error or "Gmail unavailable"}
        if not message_id:
            return {"error": "message_id is required"}
        try:
            msg = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception as e:
            return {"error": f"Gmail get failed: {e}"}

        compact = _compact_message(msg)
        compact["body"] = _extract_plain_body(msg)
        compact["attachments"] = _list_attachments(msg)
        return compact

    # ----------------------------------------------------------------- #
    # Attachment
    # ----------------------------------------------------------------- #

    def get_attachment(self, message_id: str, attachment_id: str) -> Dict[str, Any]:
        """Download and base64-decode a specific attachment.

        Returns:
            {"filename": str, "mime_type": str, "bytes": bytes, "size": int}
            or {"error": "..."} on failure.
        """
        if not self.available():
            return {"error": self._error or "Gmail unavailable"}
        if not message_id or not attachment_id:
            return {"error": "message_id and attachment_id are required"}
        try:
            att = (
                self._service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
        except Exception as e:
            return {"error": f"Gmail attachment fetch failed: {e}"}

        data = att.get("data") or ""
        try:
            raw = base64.urlsafe_b64decode(data.encode("utf-8") + b"==")
        except Exception as e:
            return {"error": f"Could not decode attachment: {e}"}

        # We also need the filename + mime type, which live on the parent
        # message's part metadata rather than on the attachment response.
        meta = _find_attachment_meta(self._service, message_id, attachment_id)
        return {
            "filename": meta.get("filename") or "attachment",
            "mime_type": meta.get("mime_type") or "application/octet-stream",
            "bytes": raw,
            "size": len(raw),
        }


# --------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------- #

def _headers_dict(msg: Dict[str, Any]) -> Dict[str, str]:
    """Convert Gmail's headers list of {name,value} dicts into a flat dict.
    Case-preserving — Gmail headers are case-insensitive by spec but
    we keep them as Gmail returns them for fidelity."""
    out: Dict[str, str] = {}
    for h in (msg.get("payload") or {}).get("headers", []) or []:
        name = h.get("name")
        if name:
            out[name] = h.get("value") or ""
    return out


def _compact_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the fields the LLM actually wants out of Gmail's response."""
    h = _headers_dict(msg)
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "subject": h.get("Subject") or "(no subject)",
        "from": h.get("From") or "",
        "to": h.get("To") or "",
        "date": h.get("Date") or "",
        "snippet": (msg.get("snippet") or "").strip(),
    }


def _extract_plain_body(msg: Dict[str, Any]) -> str:
    """Walk the MIME tree and return the first text/plain body we find.
    Falls back to text/html with tags stripped if no plain text part exists."""
    payload = msg.get("payload") or {}

    def _walk(part) -> Optional[str]:
        mime = (part or {}).get("mimeType") or ""
        if mime == "text/plain":
            data = (part.get("body") or {}).get("data")
            if data:
                return _b64url_to_text(data)
        for sub in (part or {}).get("parts") or []:
            found = _walk(sub)
            if found:
                return found
        return None

    body = _walk(payload)
    if body is not None:
        return body
    # Fallback: try html if no plain found.
    def _walk_html(part) -> Optional[str]:
        mime = (part or {}).get("mimeType") or ""
        if mime == "text/html":
            data = (part.get("body") or {}).get("data")
            if data:
                import re
                html = _b64url_to_text(data)
                # Cheap tag strip — for accurate HTML→text the caller can
                # pass the raw HTML to a real parser, but for grounding
                # an LLM answer the visible text is plenty.
                no_script = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
                return re.sub(r"<[^>]+>", " ", no_script)
        for sub in (part or {}).get("parts") or []:
            found = _walk_html(sub)
            if found:
                return found
        return None

    return _walk_html(payload) or ""


def _list_attachments(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of attachment manifests so the LLM can decide which
    to pull. Each entry has filename, mime_type, size, attachment_id."""
    out: List[Dict[str, Any]] = []

    def _walk(part) -> None:
        if not part:
            return
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        attach_id = body.get("attachmentId")
        if filename and attach_id:
            out.append({
                "attachment_id": attach_id,
                "filename": filename,
                "mime_type": part.get("mimeType") or "application/octet-stream",
                "size": body.get("size") or 0,
            })
        for sub in part.get("parts") or []:
            _walk(sub)

    _walk(msg.get("payload"))
    return out


def _find_attachment_meta(service, message_id: str, attachment_id: str) -> Dict[str, Any]:
    """Re-fetch the parent message (metadata format) and locate the matching
    attachment part to get filename + mime type. Costs one extra API call
    per attachment download — acceptable since the LLM rarely downloads
    bulk attachments."""
    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    except Exception:
        return {}
    for att in _list_attachments(msg):
        if att.get("attachment_id") == attachment_id:
            return {"filename": att["filename"], "mime_type": att["mime_type"]}
    return {}


def _b64url_to_text(data: str) -> str:
    """Decode Gmail's base64url-encoded body data to a utf-8 string.
    Gmail uses base64url (not standard base64) and may omit padding."""
    try:
        raw = base64.urlsafe_b64decode(data.encode("utf-8") + b"==")
        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.info(f"Could not decode Gmail body data: {e}")
        return ""
