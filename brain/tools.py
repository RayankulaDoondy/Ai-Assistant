"""Tool registry for Hunt's tool-using research mode.

This is the substrate for Phase 1 of the Information Retrieval Agent: the LLM
gets a set of named tools (OpenAI-compatible function schemas), decides which
to call given the user's goal, and the server executes them and feeds the
result back into a second LLM call that produces the grounded answer.

Architecture
------------
    User goal
       ↓
    LLM call #1 (with `tools` schema) → emits tool_calls
       ↓
    Server runs the tools (this module's executors)
       ↓
    Tool results appended to the message history
       ↓
    LLM call #2 (no tools) → streams the final grounded answer

Adding a new tool
-----------------
1. Add a schema dict to TOOL_SCHEMAS following OpenAI function-calling shape.
2. Add an executor function that accepts the parsed arg dict and returns a
   small dict-or-string (this becomes the tool result the LLM sees).
3. Register the executor in TOOL_EXECUTORS by the same name as the schema.

Tools are intentionally NARROW (one job each) and return structured data with
explicit source metadata so the LLM can cite. Vague tools produce vague answers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ====================================================================== #
# Tool schemas — sent to the LLM so it knows what's callable.
# Shape follows OpenAI's function-calling spec; OpenRouter, Groq, and
# Gemini's OpenAI-compat endpoints all accept this shape.
# ====================================================================== #

RETRIEVE_MEMORY_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "retrieve_memory",
        "description": (
            "MANDATORY for questions about the user's own history, work, or "
            "data. Searches across the user's past chats, active projects, "
            "open tasks, and uploaded documents. ALWAYS call this for "
            "questions like: 'what did we discuss about X', 'find the X "
            "code/file/note', 'what was the X error', 'remind me about X', "
            "'show me X from earlier', 'list my projects/tasks', or any "
            "question naming something the user owns or did. Returns ranked "
            "snippets with source metadata so you can cite them in your "
            "answer. Do NOT call for generic knowledge questions ('what is "
            "bubble sort'), pure computation ('what is 7×8'), or greetings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query — be specific. Combine entity names, "
                        "topics, and verbs. Example: 'MongoDB schema decisions' "
                        "rather than just 'database'."
                    ),
                },
                "types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["conversation", "project", "task", "document"],
                    },
                    "description": (
                        "Which memory types to search. Omit (or pass all four) "
                        "when the user's intent could span sources. Use a "
                        "narrower subset when the user explicitly named one "
                        "(e.g. 'find the resume I uploaded' → ['document'])."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max snippets to return. Default 3, cap 10. Prefer 3 unless the user asks for an exhaustive list.",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
}


GMAIL_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "gmail_search",
        "description": (
            "Search the user's Gmail and return message metadata (subject, "
            "from, date, snippet, message_id). Use whenever the user asks "
            "about EMAIL: 'did I get a bank statement', 'find the email from "
            "Amazon', 'check my inbox for X', 'what mail did I get from Y'. "
            "The `query` parameter accepts Gmail's powerful native search "
            "syntax — combine fields rather than typing free-form prose:\n"
            "  from:bank@example.com  →  messages from a sender\n"
            "  to:me  →  messages addressed to the user\n"
            "  subject:invoice  →  subject contains a word\n"
            "  has:attachment    →  only messages with attachments\n"
            "  after:2024/05/01  →  on or after that date\n"
            "  before:2024/06/01 →  before that date\n"
            "  newer_than:30d / older_than:1y  →  relative time\n"
            "  is:unread / is:starred / is:important\n"
            "  label:bills      →  user-managed label\n"
            "  in:inbox / in:spam / in:trash\n"
            "Combine with spaces (AND) or `OR`. Wrap multi-word phrases in "
            "quotes. Example for 'bank statement in May':\n"
            "  (from:bank OR from:hdfc OR subject:statement) "
            "after:2024/05/01 before:2024/06/01 has:attachment\n\n"
            "If the user hasn't connected Gmail yet, the tool returns an "
            "explicit 'gmail_not_connected' error — tell them to open the "
            "settings drawer and click Connect Gmail. Do NOT make up email "
            "contents when the search returns nothing or fails."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search syntax string. Combine field "
                        "operators rather than writing prose."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "How many messages to return. Default 10, cap 25. "
                        "Use a smaller number when the user asks a "
                        "specific question ('did I get X' → 5) and a "
                        "larger one for sweeps ('all bank mail this year')."
                    ),
                    "default": 10,
                    "minimum": 1,
                    "maximum": 25,
                },
            },
            "required": ["query"],
        },
    },
}


GMAIL_READ_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "gmail_read",
        "description": (
            "Fetch the full body and attachment manifest of ONE specific "
            "Gmail message. Call this AFTER gmail_search when the user "
            "asks about the contents of a specific email, or when the "
            "search snippet wasn't enough to answer (e.g. 'what does the "
            "statement say about my balance' → search → read the right "
            "message). The `message_id` must come from a prior "
            "gmail_search result — never invent one.\n\n"
            "Returns subject, from, to, date, the plain-text body, and a "
            "list of attachments (with filenames and ids — you can mention "
            "them in your answer but Hunt cannot yet download arbitrary "
            "attachments to the chat). If the user hasn't connected Gmail, "
            "the tool returns 'gmail_not_connected'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": (
                        "The Gmail message id from a previous "
                        "gmail_search hit."
                    ),
                },
            },
            "required": ["message_id"],
        },
    },
}


# Registry of every schema the LLM is told about. Ordered roughly by how
# often each tool is likely to be useful.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    RETRIEVE_MEMORY_SCHEMA,
    GMAIL_SEARCH_SCHEMA,
    GMAIL_READ_SCHEMA,
]


# ====================================================================== #
# Tool executors — run server-side when the LLM calls a tool.
# ====================================================================== #


def _normalize_for_dedupe(text: str) -> str:
    """Build a dedupe key from a snippet.

    For conversation snippets ("User: ...\\nAssistant: ..."), strip the user
    question and dedupe on the ASSISTANT response only. Otherwise dozens
    of "different phrasing, same answer" snippets all look distinct (the
    first 200 chars are dominated by the differing user question, not the
    common answer). The semantic value the user cares about is the
    assistant's content.

    For non-conversation snippets, falls back to normalizing the whole text.
    Whitespace is collapsed and case lowered, first 200 chars become the key.
    """
    import re as _re
    raw = (text or "").strip()
    if not raw:
        return ""
    # Conversation pattern: "User: ...\nAssistant: ...". Take just the
    # assistant portion so re-asks of the same question collapse together.
    m = _re.match(r"^user:\s*[\s\S]*?\nassistant:\s*([\s\S]*)$", raw, _re.IGNORECASE)
    body = m.group(1) if m else raw
    return _re.sub(r"\s+", " ", body.lower()).strip()[:200]


def _smart_title(snippet: str, meta: Dict[str, Any]) -> str:
    """Build a human-useful title for a citation card.

    Priority order:
      1. doc_title / project_name (when present)
      2. First USER turn from a conversation snippet (their actual question)
      3. First line of the snippet, trimmed

    Truncates to 70 chars with ellipsis. Falls back to the memory type
    name if nothing else works.
    """
    doc_title = meta.get("doc_title")
    if doc_title:
        return str(doc_title)[:70]
    project_name = meta.get("project_name")
    if project_name:
        return str(project_name)[:70]

    text = (snippet or "").strip()
    if not text:
        return (meta.get("type") or "memory").title()

    # Conversation snippets start with "User: <question>\nAssistant: ..." —
    # pull the user's question, that's what the user remembers asking.
    if text.startswith("User:"):
        first_line = text.split("\n", 1)[0]
        user_q = first_line[len("User:"):].strip()
        if user_q:
            return user_q[:70] + ("…" if len(user_q) > 70 else "")

    # Otherwise first non-empty line.
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line[:70] + ("…" if len(line) > 70 else "")
    return (meta.get("type") or "memory").title()


def _execute_retrieve_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a memory retrieval. Imports the store lazily so this module is
    cheap to import (the LLM engine pulls it on every request).

    Post-processing applied after the raw retrieve:
      - Dedupe near-identical snippets (the user's data has many "write
        bubble sort" attempts — we don't want 5 cards for the same code).
      - Build a smart title per citation (snippet's first user turn or
        doc/project name beats a generic "Past chat" badge).
    """
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "empty query", "results": [], "count": 0}

    types = args.get("types") or None
    if types and not isinstance(types, list):
        types = None
    if types:
        valid = {"conversation", "project", "task", "document"}
        types = [t for t in types if t in valid]
        if not types:
            types = None

    limit = args.get("limit", 5)
    try:
        limit = max(1, min(10, int(limit)))
    except (TypeError, ValueError):
        limit = 5

    try:
        from memory.memory_store import get_memory_store
    except Exception as e:
        logger.warning(f"retrieve_memory: store import failed ({e})")
        return {"error": "memory store unavailable", "results": [], "count": 0}

    store = get_memory_store()
    try:
        # Over-fetch so dedupe has room to trim back to `limit`.
        hits = store.retrieve(query=query, limit=limit * 2, types=types)
    except Exception as e:
        logger.warning(f"retrieve_memory call failed: {e}")
        return {"error": str(e), "results": [], "count": 0}

    # Self-reference guard: ConversationMemory.add_exchange() writes the
    # current turn into Chroma AFTER the reply finishes. A quick follow-up
    # (within ~1-2 minutes) can otherwise retrieve the just-stored exchange
    # as its own "source", creating an echo chamber. Skip anything younger
    # than this cutoff. Fail-open: items with no timestamp are kept.
    cutoff_iso = (datetime.now() - timedelta(seconds=120)).isoformat()

    # Dedupe: skip anything whose normalized first-200-char prefix matches
    # an already-kept snippet. Preserves rank order from retrieve().
    seen_keys: set = set()
    compact: List[Dict[str, Any]] = []
    for h in hits:
        snippet = (h.get("content") or "")[:400]
        key = _normalize_for_dedupe(snippet)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        meta = h.get("metadata") or {}

        # Drop fresh self-reference candidates. ISO 8601 timestamps from
        # the same datetime.now().isoformat() format sort lexicographically,
        # so string comparison is sound and avoids parse failures.
        ts = meta.get("timestamp")
        if isinstance(ts, str) and ts and ts > cutoff_iso:
            continue

        compact.append({
            "snippet": snippet,
            "title": _smart_title(snippet, meta),
            "type": meta.get("type") or "memory",
            "id": h.get("id"),
            "project_id": meta.get("project_id"),
            "project_name": meta.get("project_name"),
            "doc_id": meta.get("doc_id"),
            "doc_title": meta.get("doc_title"),
            "timestamp": ts,
        })
        if len(compact) >= limit:
            break

    return {"query": query, "results": compact, "count": len(compact)}


# --------------------------------------------------------------------- #
# Gmail tools — Phase 3
# --------------------------------------------------------------------- #
# Both `gmail_search` and `gmail_read` lean on integrations.gmail_client.
# The wrapper handles OAuth load/refresh, so the executors stay tiny and
# focused on shaping the result for the LLM + Hunt's citation rendering.

def _execute_gmail_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """LLM-callable Gmail search. Translates GmailClient.search() results
    into the same `{results: [...], count: N}` shape `retrieve_memory`
    uses, so the citation-card pipeline in main.py picks them up without
    a special case."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "empty query", "results": [], "count": 0}

    max_results = args.get("max_results", 10)
    try:
        max_results = max(1, min(25, int(max_results)))
    except (TypeError, ValueError):
        max_results = 10

    try:
        from integrations.gmail_client import GmailClient
    except Exception as e:
        logger.warning(f"gmail_search: import failed ({e})")
        return {"error": f"gmail integration unavailable: {e}", "results": [], "count": 0}

    client = GmailClient()
    if not client.available():
        # The LLM is instructed (via tool description) to surface this
        # cleanly: "you need to connect Gmail — settings drawer".
        return {
            "error": "gmail_not_connected",
            "message": client.error() or "Gmail not connected.",
            "results": [],
            "count": 0,
        }

    raw = client.search(query, max_results=max_results)
    if raw.get("error"):
        return {"error": raw["error"], "results": [], "count": 0}

    # Reshape into the citation-friendly format. Snippet ≈ what the LLM
    # sees + what the UI renders under the card. Title = subject line.
    compact: List[Dict[str, Any]] = []
    for m in raw.get("results") or []:
        compact.append({
            "snippet": (m.get("snippet") or "")[:400],
            "title": m.get("subject") or "(no subject)",
            "type": "gmail",
            "id": m.get("id"),
            "message_id": m.get("id"),
            "thread_id": m.get("thread_id"),
            "from": m.get("from") or "",
            "to": m.get("to") or "",
            "date": m.get("date") or "",
            # Mirror conversation citations: a parseable timestamp helps
            # the UI sort and the dedupe layer treat fresh hits sanely.
            "timestamp": m.get("date") or None,
            # Per-source metadata the citation rendering already understands
            # is mostly project/document specific — Gmail has no analogue,
            # so we leave those slots empty.
            "project_id": None,
            "project_name": None,
            "doc_id": None,
            "doc_title": None,
        })

    return {
        "query": query,
        "results": compact,
        "count": len(compact),
        "user_email": raw.get("user_email"),
    }


def _execute_gmail_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """LLM-callable Gmail read. Returns headers + plain body + attachment
    manifest so the model can ground its answer. Body is truncated server-
    side so we don't blow the model's context window on a giant newsletter."""
    message_id = str(args.get("message_id") or "").strip()
    if not message_id:
        return {"error": "message_id is required"}

    try:
        from integrations.gmail_client import GmailClient
    except Exception as e:
        logger.warning(f"gmail_read: import failed ({e})")
        return {"error": f"gmail integration unavailable: {e}"}

    client = GmailClient()
    if not client.available():
        return {"error": "gmail_not_connected", "message": client.error() or "Gmail not connected."}

    msg = client.get_message(message_id)
    if msg.get("error"):
        return {"error": msg["error"]}

    # Cap the body so a giant marketing email doesn't dominate the model's
    # context. The LLM can re-call gmail_read for a different message if
    # it needs more from a different thread; truncating here trades a tiny
    # bit of fidelity for predictable token cost.
    body = msg.get("body") or ""
    truncated = False
    BODY_CAP = 6000
    if len(body) > BODY_CAP:
        body = body[:BODY_CAP]
        truncated = True

    return {
        "message_id": msg.get("id"),
        "thread_id": msg.get("thread_id"),
        "subject": msg.get("subject"),
        "from": msg.get("from"),
        "to": msg.get("to"),
        "date": msg.get("date"),
        "body": body,
        "body_truncated": truncated,
        "attachments": msg.get("attachments") or [],
    }


TOOL_EXECUTORS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "retrieve_memory": _execute_retrieve_memory,
    "gmail_search":    _execute_gmail_search,
    "gmail_read":      _execute_gmail_read,
}


# ====================================================================== #
# Public API
# ====================================================================== #


def list_tools() -> List[Dict[str, Any]]:
    """Return the JSON-serializable list of tool schemas to send to the LLM."""
    return list(TOOL_SCHEMAS)


def execute(name: str, raw_arguments: Any) -> Dict[str, Any]:
    """Dispatch one tool call.

    `raw_arguments` is what the LLM produced for `function.arguments` — usually
    a JSON string, occasionally already a dict (some providers parse for us).
    Returns the executor's result dict. Errors are NEVER raised — they come
    back as `{"error": "..."}` so the LLM sees them and can recover (e.g. try
    a different query).
    """
    fn = TOOL_EXECUTORS.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}

    if isinstance(raw_arguments, str):
        try:
            args = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as e:
            return {"error": f"could not parse tool arguments: {e}"}
    elif isinstance(raw_arguments, dict):
        args = raw_arguments
    else:
        args = {}

    try:
        return fn(args) or {}
    except Exception as e:
        logger.error(f"Tool {name!r} crashed: {e}", exc_info=True)
        return {"error": str(e)}


def summarize_tool_call_for_ui(name: str, args: Dict[str, Any]) -> str:
    """Tiny human-readable string the UI can show as a 'searching memory...'
    indicator while the tool runs. Specific tools get a tailored message;
    others get a generic 'running <name>...'."""
    if name == "retrieve_memory":
        q = (args or {}).get("query") or ""
        types = (args or {}).get("types") or []
        scope = "memory" if not types else " + ".join(types)
        return f"Searching {scope} for: {q[:60]}"
    if name == "gmail_search":
        q = (args or {}).get("query") or ""
        return f"Searching Gmail for: {q[:80]}"
    if name == "gmail_read":
        mid = (args or {}).get("message_id") or ""
        return f"Reading Gmail message {mid[:12]}…"
    return f"Running {name}…"
