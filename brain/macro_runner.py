"""Voice macros — composite actions Hunt runs when a "macro intent" fires.

A macro is a small, deterministic recipe that combines reads (and sometimes
one cheap LLM call) into a single user-visible reply. Macros bypass the chat
LLM call entirely — the assistant's response is the macro's script, generated
in Python from real data.

Macro registry:
    morning_brief    → BriefingComposer.compose() + read briefing.speakable_script
    read_open_tasks  → list active project's tasks; no LLM call
    wrap_up_session  → llm.summarize_text(current verbatim) + append to project notes

Design rules:
    - Each macro is a function: `run(ctx) -> MacroResult`.
    - `ctx` is a plain dict the caller fills with whatever singletons the macro
      might need (briefing_composer, project_store, llm_engine, …). Macros
      ignore keys they don't use; new macros need no plumbing changes.
    - `MacroResult` carries everything: speakable script (for TTS), structured
      data (for UI cards), and a `side_effects` list describing any writes
      the macro performed, so the caller can log them.
    - Side effects MUST be marked. Currently only `wrap_up_session` writes
      (it appends to project notes). Adding a write without listing it here
      is a bug worth catching in review.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Intents that should short-circuit the LLM pipeline and dispatch to a macro.
# Kept narrow so casual phrases never trigger composite actions accidentally.
MACRO_INTENTS = ("morning_brief", "read_open_tasks", "wrap_up_session")


@dataclass
class MacroResult:
    name: str
    script: str                          # what TTS reads out loud
    structured: Dict[str, Any] = field(default_factory=dict)
    side_effects: List[str] = field(default_factory=list)
    failed: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------- macros

def _macro_morning_brief(ctx: Dict[str, Any]) -> MacroResult:
    """Compose and read aloud the current briefing. No writes."""
    composer = ctx.get("briefing_composer")
    if not composer:
        return MacroResult(
            name="morning_brief",
            script="Briefing isn't available right now.",
            failed=True,
            error="briefing_composer missing",
        )
    payload = composer.compose()
    return MacroResult(
        name="morning_brief",
        script=payload.speakable_script,
        structured=payload.to_dict(),
    )


def _macro_read_open_tasks(ctx: Dict[str, Any]) -> MacroResult:
    """Read out the open tasks on the active project. No writes."""
    project_store = ctx.get("project_store")
    conversation_memory = ctx.get("conversation_memory")
    if not project_store or not conversation_memory:
        return MacroResult(
            name="read_open_tasks",
            script="I can't reach the project store right now.",
            failed=True,
            error="missing project_store or conversation_memory",
        )
    pid = conversation_memory.active_project_id
    if not pid:
        return MacroResult(
            name="read_open_tasks",
            script="No active project. Pick one from the Memory panel and I'll read its tasks.",
            structured={"active_project_id": None},
        )
    proj = project_store.get(pid)
    if not proj:
        return MacroResult(
            name="read_open_tasks",
            script="The active project is gone.",
            failed=True,
            error="project not found",
        )
    open_tasks = [t for t in (proj.get("open_tasks") or []) if not t.get("done")]
    if not open_tasks:
        return MacroResult(
            name="read_open_tasks",
            script=f"On {proj.get('name','your project')}, no open tasks.",
            structured={"project_name": proj.get("name"), "tasks": []},
        )
    spoken = ", ".join(t["text"] for t in open_tasks[:5])
    extra = "" if len(open_tasks) <= 5 else f" Plus {len(open_tasks) - 5} more."
    return MacroResult(
        name="read_open_tasks",
        script=f"On {proj.get('name','your project')}, {len(open_tasks)} open: {spoken}.{extra}",
        structured={
            "project_name": proj.get("name"),
            "tasks": [{"id": t.get("id"), "text": t["text"]} for t in open_tasks],
        },
    )


def _macro_wrap_up_session(ctx: Dict[str, Any]) -> MacroResult:
    """End-of-session helper:
        1. Summarize the current verbatim chat via the LLM summarizer.
        2. Append the summary as a dated note to the active project (if any).
        3. Speak a short confirmation with the open-task count.
    The summary call reuses the existing low-temperature, no-hallucinate
    summarizer prompt, so this macro doesn't introduce a second LLM persona.
    """
    llm = ctx.get("llm_engine")
    conv = ctx.get("conversation_memory")
    project_store = ctx.get("project_store")
    if not llm or not conv:
        return MacroResult(
            name="wrap_up_session",
            script="I can't wrap up — the chat state isn't reachable.",
            failed=True,
            error="missing llm_engine or conversation_memory",
        )
    turns = conv.current_session
    if not turns:
        return MacroResult(
            name="wrap_up_session",
            script="Nothing to wrap up — this chat is empty.",
            structured={"summary": "", "turn_count": 0},
        )
    exchanges_text = "\n\n".join(
        f"User: {(t.get('user') or '').strip()}\n"
        f"Assistant: {(t.get('assistant') or '').strip()}"
        for t in turns
    )
    try:
        summary = llm.summarize_text(
            exchanges_text=exchanges_text,
            prior_summary=conv.get_running_summary() or "",
        )
    except Exception as e:
        logger.warning(f"wrap_up_session summarize failed: {e}")
        summary = ""

    side_effects: List[str] = []
    project_name = None
    open_task_count = 0
    if project_store and conv.active_project_id:
        proj = project_store.get(conv.active_project_id)
        if proj:
            project_name = proj.get("name")
            open_task_count = sum(
                1 for t in (proj.get("open_tasks") or []) if not t.get("done")
            )
            # Append the summary as a new note line under a dated heading.
            if summary:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                addition = f"\n\n[Session wrap, {stamp}]\n{summary.strip()}"
                merged = ((proj.get("notes") or "") + addition).strip()
                project_store.patch(proj["id"], notes=merged)
                side_effects.append(
                    f"appended {len(addition)} chars to project '{project_name}' notes"
                )

    bits: List[str] = ["Saved."]
    if project_name:
        if open_task_count == 0:
            bits.append(f"On {project_name}, no open tasks remain.")
        elif open_task_count == 1:
            bits.append(f"On {project_name}, one open task remains.")
        else:
            bits.append(f"On {project_name}, {open_task_count} open tasks remain.")
    else:
        bits.append("No active project, so the summary stays in the chat memory only.")
    return MacroResult(
        name="wrap_up_session",
        script=" ".join(bits),
        structured={
            "summary": summary,
            "turn_count": len(turns),
            "project_name": project_name,
            "open_task_count": open_task_count,
        },
        side_effects=side_effects,
    )


# ----------------------------------------------------------- registry/runner

_REGISTRY: Dict[str, Callable[[Dict[str, Any]], MacroResult]] = {
    "morning_brief":   _macro_morning_brief,
    "read_open_tasks": _macro_read_open_tasks,
    "wrap_up_session": _macro_wrap_up_session,
}


def is_macro(intent: Optional[str]) -> bool:
    return bool(intent) and intent in _REGISTRY


def run_macro(intent: str, ctx: Dict[str, Any]) -> MacroResult:
    """Dispatch. Catches any unexpected error so /chat never crashes on a bad
    macro — the chat just gets a fallback script."""
    fn = _REGISTRY.get(intent)
    if not fn:
        return MacroResult(
            name=intent or "unknown",
            script=f"Unknown macro {intent!r}.",
            failed=True,
            error="unknown macro",
        )
    try:
        return fn(ctx)
    except Exception as e:
        logger.error(f"Macro {intent} crashed: {e}")
        return MacroResult(
            name=intent,
            script="Something went wrong running that.",
            failed=True,
            error=str(e),
        )
