"""Google Calendar OAuth flow + token persistence.

Companion to integrations/gmail_auth.py — same OAuth scaffolding (PKCE,
state persistence, client_secret discovery), but a separate token file so
the user can connect/disconnect Calendar independently of Gmail.

We use the same client_secret.json as Gmail (one OAuth app per Google
Cloud project covers all scopes), but request a different scope set and
keep the resulting token in calendar_token.json so revoking Gmail
doesn't kill Calendar and vice versa.

Layout on disk:
    data/google/client_secret.json   — OAuth app credentials (shared
                                        with Gmail; same file)
    data/google/calendar_token.json  — calendar tokens (separate from
                                        gmail's token.json)

Scope: calendar.readonly only. Read events, don't create or modify.
A later phase can add calendar.events for create/update once we wire
an action chip for it.

Public surface mirrors gmail_auth — is_connected, connected_email,
start_auth_url, complete_auth, load_credentials, revoke, status.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Google's `include_granted_scopes=true` makes the token response include
# ALL scopes the user has ever granted this OAuth client (Gmail's, etc.),
# not just the ones we asked for in THIS flow. google-auth-oauthlib then
# refuses the token with "Scope has changed from X to X+Y" — even though
# the superset is benign. Relaxing scope validation accepts it. This env
# var is read by oauthlib at validation time, so setting it here at import
# works whether complete_auth() is called once or many times.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# --------------------------------------------------------------------- #
# Paths + constants
# --------------------------------------------------------------------- #
_BASE_DIR = os.path.join(
    os.environ.get("DATA_DIR")
    or os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/data",
    "google",
)
CLIENT_SECRET_PATH = os.path.join(_BASE_DIR, "client_secret.json")
# Different file from Gmail's so the two grants are independent.
TOKEN_PATH = os.path.join(_BASE_DIR, "calendar_token.json")
# PKCE round-trip persistence — same file Gmail uses, since state strings
# are random UUIDs and never collide. Keeps a single source of truth.
PENDING_STATE_PATH = os.path.join(_BASE_DIR, "pending_oauth_states.json")

# Read-only. NEVER add calendar / calendar.events without an explicit
# UI surface for "Hunt is about to create an event on your calendar".
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

def _ensure_dir() -> None:
    os.makedirs(_BASE_DIR, exist_ok=True)


def _client_config_loaded() -> bool:
    return os.path.exists(CLIENT_SECRET_PATH)


def _read_token_file() -> Optional[Dict[str, Any]]:
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        logger.warning(f"Could not read Calendar token file: {e}")
        return None


def _write_token_file(payload: Dict[str, Any]) -> None:
    _ensure_dir()
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, TOKEN_PATH)


# One-slot cache for the email looked up post-exchange so it's folded
# into the persisted token. Reset after revoke().
payload_email_cache: Dict[str, Optional[str]] = {"value": None}


def _credentials_to_dict(creds) -> Dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        "hunt_email": payload_email_cache.get("value"),
    }


# --------------------------------------------------------------------- #
# PKCE state persistence — shared file with Gmail (state strings are
# unique per flow so they never collide; the matching code_verifier is
# what's interesting, not which integration started the flow).
# --------------------------------------------------------------------- #
_PENDING_TTL_SECONDS = 600


def _read_pending_states() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(PENDING_STATE_PATH):
        return {}
    try:
        with open(PENDING_STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Could not read pending OAuth states: {e}")
        return {}


def _write_pending_states(states: Dict[str, Dict[str, Any]]) -> None:
    _ensure_dir()
    tmp = PENDING_STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(states, fh)
    os.replace(tmp, PENDING_STATE_PATH)


def _prune_pending(states: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    import time
    now = time.time()
    return {
        s: v for s, v in states.items()
        if isinstance(v, dict) and (now - float(v.get("ts") or 0)) < _PENDING_TTL_SECONDS
    }


def _save_pending(state: str, code_verifier: str) -> None:
    import time
    states = _prune_pending(_read_pending_states())
    states[state] = {"code_verifier": code_verifier, "ts": time.time()}
    _write_pending_states(states)


def _take_pending(state: str) -> Optional[str]:
    states = _prune_pending(_read_pending_states())
    entry = states.pop(state, None)
    _write_pending_states(states)
    if not entry:
        return None
    return entry.get("code_verifier")


# --------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------- #

def is_connected() -> bool:
    tok = _read_token_file()
    return bool(tok and tok.get("refresh_token"))


def connected_email() -> Optional[str]:
    tok = _read_token_file()
    if not tok:
        return None
    return tok.get("hunt_email")


def start_auth_url(redirect_uri: str) -> str:
    """Build the Google consent URL for the Calendar scope."""
    if not _client_config_loaded():
        raise FileNotFoundError(
            f"client_secret.json missing at {CLIENT_SECRET_PATH}. "
            "Complete the Google Cloud setup steps in GMAIL_SETUP.md first "
            "(the same OAuth client covers Gmail and Calendar)."
        )
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH, scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        _save_pending(state, code_verifier)
    return auth_url


def complete_auth(code: str, redirect_uri: str, state: Optional[str] = None) -> Dict[str, Any]:
    """Exchange the authorization code for tokens and persist them."""
    if not _client_config_loaded():
        return {"error": "client_secret.json not configured"}

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH, scopes=SCOPES, redirect_uri=redirect_uri
    )
    if state:
        code_verifier = _take_pending(state)
        if code_verifier:
            flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    creds = flow.credentials

    email = _lookup_user_email(creds)
    payload_email_cache["value"] = email

    payload = _credentials_to_dict(creds)
    _write_token_file(payload)
    logger.info(f"Calendar OAuth: connected as {email or 'unknown'}")
    return {"connected": True, "email": email}


def _lookup_user_email(creds) -> Optional[str]:
    """Hit Calendar's calendarList endpoint to discover which account this is.
    Returns the primary calendar's ID (which IS the account email for
    Google personal accounts). Falls back to None on any error."""
    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        primary = service.calendarList().get(calendarId="primary").execute()
        # For personal Google accounts, the primary calendar's id is the
        # user's email; for workspace it may be something else but is still
        # the canonical identifier for "this calendar account".
        return primary.get("id")
    except Exception as e:
        logger.info(f"Could not look up Calendar user email post-auth: {e}")
        return None


def load_credentials():
    """Return rebuilt Credentials, auto-refreshing if needed."""
    tok = _read_token_file()
    if not tok or not tok.get("refresh_token"):
        return None

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri"),
        client_id=tok.get("client_id"),
        client_secret=tok.get("client_secret"),
        scopes=tok.get("scopes") or SCOPES,
    )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                fresh = _credentials_to_dict(creds)
                fresh["hunt_email"] = tok.get("hunt_email")
                _write_token_file(fresh)
            except Exception as e:
                logger.warning(f"Calendar token refresh failed: {e}. User needs to reconnect.")
                return None
        else:
            return None
    return creds


def revoke() -> bool:
    """Disconnect Calendar — revoke at Google, then wipe local token file."""
    tok = _read_token_file()
    if not tok:
        return False
    try:
        import requests
        access_token = tok.get("token")
        if access_token:
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": access_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
    except Exception as e:
        logger.info(f"Calendar revoke call failed (non-fatal): {e}")
    try:
        os.remove(TOKEN_PATH)
    except FileNotFoundError:
        pass
    payload_email_cache["value"] = None
    logger.info("Calendar OAuth: disconnected")
    return True


def status() -> Dict[str, Any]:
    return {
        "client_configured": _client_config_loaded(),
        "connected": is_connected(),
        "email": connected_email(),
        "client_secret_path": CLIENT_SECRET_PATH,
        "scopes": SCOPES,
    }
