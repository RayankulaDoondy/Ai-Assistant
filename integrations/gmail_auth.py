"""Gmail OAuth flow + token persistence.

Phase 3 of Hunt's Information Retrieval Agent. This module owns the
"connect Gmail" half of the integration; `gmail_client.py` owns the
"use the connection" half.

Layout on disk:
    data/google/client_secret.json   — OAuth app credentials (user-supplied,
                                        downloaded from Google Cloud Console;
                                        see GMAIL_SETUP.md)
    data/google/token.json           — encrypted-at-rest OAuth tokens
                                        (written after a successful flow;
                                        ignored by git via the data/ rule)

Scope: gmail.readonly only. We never request write/send/modify scopes;
Phase 3's contract is "find and read", not "act on".

Public surface
--------------
    is_connected()                      -> bool
    connected_email()                   -> Optional[str]
    start_auth_url(redirect_uri)        -> str    # returns Google's consent URL
    complete_auth(code, redirect_uri)   -> dict   # exchanges code → token, persists
    load_credentials()                  -> google.oauth2.credentials.Credentials | None
    revoke()                            -> bool   # disconnect

Errors are returned as dicts with an "error" key when called from request
handlers; internal-only helpers raise. Tokens are stored in plain JSON
because the data/ directory is gitignored and Hunt's local-Docker threat
model treats the host filesystem as trusted. If you ever deploy this to
shared infrastructure, swap json.dump for an encrypted store.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- #
# Paths + constants
# --------------------------------------------------------------------- #
_BASE_DIR = os.path.join(
    os.environ.get("DATA_DIR")
    or os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/data",
    "google",
)
CLIENT_SECRET_PATH = os.path.join(_BASE_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(_BASE_DIR, "token.json")
# PKCE round-trip: google-auth-oauthlib generates a code_verifier during
# start_auth_url() and requires it back on fetch_token(). The two calls
# happen in different HTTP requests (different Python objects), so we
# persist {state: code_verifier} between them. Entries are deleted after
# a successful exchange; we lazily prune entries older than 10 min so
# abandoned flows don't pile up.
PENDING_STATE_PATH = os.path.join(_BASE_DIR, "pending_oauth_states.json")

# Read-only access only. NEVER add gmail.send / gmail.modify / gmail.labels
# without an explicit user-facing UI change — Hunt's contract with the user
# is that we can find and read mail, not act on it.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

def _ensure_dir() -> None:
    """Create the google/ subdirectory if it doesn't exist yet."""
    os.makedirs(_BASE_DIR, exist_ok=True)


def _client_config_loaded() -> bool:
    """Has the user dropped client_secret.json in place yet?"""
    return os.path.exists(CLIENT_SECRET_PATH)


def _read_token_file() -> Optional[Dict[str, Any]]:
    """Return the persisted token dict, or None when nothing's saved yet."""
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        logger.warning(f"Could not read Gmail token file: {e}")
        return None


def _write_token_file(payload: Dict[str, Any]) -> None:
    """Persist the token. Atomic-ish: write to .tmp then rename."""
    _ensure_dir()
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, TOKEN_PATH)


def _credentials_to_dict(creds) -> Dict[str, Any]:
    """Serialize google.oauth2.credentials.Credentials to a dict we can JSON.

    We save the FULL dict (including refresh_token) because we need it to
    survive process restarts. Google's lib re-hydrates from this exact shape.
    """
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        # Hunt-internal: cache the user's email so /auth/google/status can
        # answer "connected as who?" without a Gmail API call.
        "hunt_email": payload_email_cache.get("value"),
    }


# Tiny one-slot cache used during complete_auth() to remember the email
# we looked up post-exchange so it can be folded into the persisted token.
payload_email_cache: Dict[str, Optional[str]] = {"value": None}


# --------------------------------------------------------------------- #
# PKCE state persistence — maps OAuth `state` to the matching code_verifier
# so the callback request can reconstruct the Flow with the right secret.
# --------------------------------------------------------------------- #
_PENDING_TTL_SECONDS = 600  # 10 min — generous to allow slow consent flows.


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
    """Drop any entry older than _PENDING_TTL_SECONDS so abandoned auth
    attempts don't accumulate."""
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
    """Look up and remove the code_verifier for this state. Atomic-ish:
    we read, mutate, and write back to disk in one shot."""
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
    """True when a usable token (with a refresh_token) is on disk.

    Doesn't validate freshness — `load_credentials()` will refresh on use.
    """
    tok = _read_token_file()
    return bool(tok and tok.get("refresh_token"))


def connected_email() -> Optional[str]:
    """Return the Gmail address the persisted token belongs to (cached
    during complete_auth). Returns None when no token is saved."""
    tok = _read_token_file()
    if not tok:
        return None
    return tok.get("hunt_email")


def start_auth_url(redirect_uri: str) -> str:
    """Build the Google consent URL the user is redirected to.

    Raises FileNotFoundError if client_secret.json hasn't been placed yet —
    the caller (a Flask/FastAPI route) should translate that into a 400
    explaining the setup is incomplete.
    """
    if not _client_config_loaded():
        raise FileNotFoundError(
            f"Gmail client_secret.json missing at {CLIENT_SECRET_PATH}. "
            "Complete the Google Cloud setup steps in GMAIL_SETUP.md first."
        )
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH, scopes=SCOPES, redirect_uri=redirect_uri
    )
    # `access_type=offline` gives us a refresh_token; `prompt=consent`
    # forces Google to re-issue it even if the user already authorized
    # this app once, which avoids the "you have no refresh token" trap.
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # PKCE: persist the auto-generated code_verifier so the callback can
    # round-trip it back to Google for the token exchange. Without this
    # the exchange fails with "invalid_grant: Missing code verifier."
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        _save_pending(state, code_verifier)
    return auth_url


def complete_auth(code: str, redirect_uri: str, state: Optional[str] = None) -> Dict[str, Any]:
    """Exchange the authorization code returned by Google for tokens,
    persist them, and return a small success dict the route can echo to the
    user. Raises on protocol-level failures so the route can map them to
    a 400/500 response.

    `state` is the OAuth state parameter Google echoes back; we use it
    to look up the matching PKCE code_verifier we persisted during
    start_auth_url. Without that verifier the token exchange will fail.
    """
    if not _client_config_loaded():
        return {"error": "client_secret.json not configured"}

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH, scopes=SCOPES, redirect_uri=redirect_uri
    )
    # Restore the PKCE code_verifier from the start request. If state is
    # missing or unknown (browser tampering, expired flow), fetch_token
    # will fail with a Google-side error — which is the correct response.
    if state:
        code_verifier = _take_pending(state)
        if code_verifier:
            flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Look up the user's email so connected_email() can answer without
    # hitting Gmail again. We use the userinfo endpoint instead of Gmail
    # API to keep this independent of the gmail_client module.
    email = _lookup_user_email(creds)
    payload_email_cache["value"] = email

    payload = _credentials_to_dict(creds)
    _write_token_file(payload)
    logger.info(f"Gmail OAuth: connected as {email or 'unknown'}")
    return {"connected": True, "email": email}


def _lookup_user_email(creds) -> Optional[str]:
    """Hit Google's tokeninfo endpoint to discover which email signed in.

    Cheap (single HTTP call), avoids pulling in the full Gmail client just
    to learn an email. Returns None if the call fails — callers tolerate it.
    """
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from googleapiclient.discovery import build

        # The "userinfo" endpoint on the OAuth2 v2 service returns
        # {"email": "...", "name": "...", ...} with the openid/email scope.
        # We can also get it from the Gmail API's getProfile, which we
        # already have permission for via gmail.readonly.
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        prof = service.users().getProfile(userId="me").execute()
        return prof.get("emailAddress")
    except Exception as e:
        logger.info(f"Could not look up Gmail user email post-auth: {e}")
        return None


def load_credentials():
    """Return google.oauth2.credentials.Credentials rebuilt from the token
    file, or None when not connected. Auto-refreshes if expired.

    Callers are the Gmail client wrapper and tools that talk to Gmail.
    """
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

    # Refresh if the access token is expired. Google's library handles
    # the "almost expired" case for us. If refresh fails (revoked,
    # expired refresh token, etc.) we surface None so the caller can
    # tell the user to reconnect.
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Persist the refreshed access token so we don't refresh
                # on every single request.
                fresh = _credentials_to_dict(creds)
                # Preserve the cached email — _credentials_to_dict reads
                # from the module-level cache which isn't set during refresh.
                fresh["hunt_email"] = tok.get("hunt_email")
                _write_token_file(fresh)
            except Exception as e:
                logger.warning(f"Gmail token refresh failed: {e}. User needs to reconnect.")
                return None
        else:
            return None
    return creds


def revoke() -> bool:
    """Disconnect Gmail. Best-effort revokes the token at Google's end so
    the user's authorized-apps list stays clean, then deletes the local
    token file. Returns True if a token was removed locally, False if
    nothing was connected in the first place.
    """
    tok = _read_token_file()
    if not tok:
        return False
    try:
        import requests
        access_token = tok.get("token")
        if access_token:
            # Google's revoke endpoint accepts either access_token or
            # refresh_token. Either kills the grant on Google's side.
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": access_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
    except Exception as e:
        # Non-fatal — even if Google rejects (token already expired etc.),
        # we still wipe the local file so Hunt forgets the connection.
        logger.info(f"Gmail revoke call failed (non-fatal): {e}")
    try:
        os.remove(TOKEN_PATH)
    except FileNotFoundError:
        pass
    payload_email_cache["value"] = None
    logger.info("Gmail OAuth: disconnected")
    return True


# --------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------- #

def status() -> Dict[str, Any]:
    """A small JSON-serializable dict the UI can render as a status badge."""
    return {
        "client_configured": _client_config_loaded(),
        "connected": is_connected(),
        "email": connected_email(),
        "client_secret_path": CLIENT_SECRET_PATH,
        "scopes": SCOPES,
    }
