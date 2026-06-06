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
                    "description": "Max snippets to return. Default 5, cap 10.",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
}


# Registry of every schema the LLM is told about. Ordered roughly by how
# often each tool is likely to be useful.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    RETRIEVE_MEMORY_SCHEMA,
]


# ====================================================================== #
# Tool executors — run server-side when the LLM calls a tool.
# ====================================================================== #


def _execute_retrieve_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a memory retrieval. Imports the store lazily so this module is
    cheap to import (the LLM engine pulls it on every request)."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "empty query", "results": [], "count": 0}

    types = args.get("types") or None
    if types and not isinstance(types, list):
        types = None
    if types:
        # Defensive: cap to the four valid types.
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
        hits = store.retrieve(query=query, limit=limit, types=types)
    except Exception as e:
        logger.warning(f"retrieve_memory call failed: {e}")
        return {"error": str(e), "results": [], "count": 0}

    # Trim each snippet so the tool result the LLM sees stays compact.
    # The full content is preserved server-side for citation rendering.
    compact = []
    for h in hits:
        meta = h.get("metadata") or {}
        compact.append({
            "snippet": (h.get("content") or "")[:400],
            "type": meta.get("type") or "memory",
            "id": h.get("id"),
            # Tiny per-type metadata that's useful for citations:
            "project_id": meta.get("project_id"),
            "project_name": meta.get("project_name"),
            "doc_id": meta.get("doc_id"),
            "doc_title": meta.get("doc_title"),
            "timestamp": meta.get("timestamp"),
        })
    return {"query": query, "results": compact, "count": len(compact)}


TOOL_EXECUTORS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "retrieve_memory": _execute_retrieve_memory,
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
    return f"Running {name}…"
