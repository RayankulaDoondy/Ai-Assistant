"""External-service integrations (Phase 3+).

Each integration lives in its own module here. The pattern is:
    - <service>_auth.py   — OAuth flow + token storage
    - <service>_client.py — API wrapper that uses the stored token

Tools that the LLM can call (e.g. gmail_search) live in brain/tools.py and
delegate to these client classes.
"""
