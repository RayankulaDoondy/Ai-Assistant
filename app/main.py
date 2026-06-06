"""
Main Application - FastAPI Backend
"""
import logging
import os
import re
import json as _json

import uuid
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from html import unescape

from config import settings
from brain import (
    get_llm_engine,
    get_context_manager,
    get_reasoning,
    get_model_router,
    INTENT_TO_ROLE,
    KNOWN_ROLES,
    propose_action,
    ACTION_INTENTS,
    MACRO_INTENTS,
    is_macro,
    run_macro,
)
from app.briefing import BriefingComposer
from memory import (
    get_memory_store,
    ConversationMemory,
    extract_facts,
    get_profile_store,
    PROFILE_FIELDS,
    get_approval_store,
    fact_pattern_keys,
    get_mongo_sync,
    mongo_sync_singleton,
    get_project_store,
    PROJECT_STATUSES,
)
from voice import get_stt, get_tts, get_wakeword_detector
from automation import (
    get_desktop_automation,
    get_browser_automation,
    run_action,
    get_action_history,
    ACTION_REGISTRY,
)

# Setup logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Hunt - Personal AI Assistant"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = os.path.join(settings.BASE_DIR, "ui", "static")
if os.path.isdir(UI_DIR):
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

# Request/Response models
class ChatRequest(BaseModel):
    """Chat request model"""
    message: str
    include_context: bool = True
    memory_type: Optional[str] = None
    use_live_search: bool = False
    voice_mode: bool = False
    # UI-tunable reply controls. Both are optional; backend falls back to settings.
    response_length: Optional[str] = None  # "short" | "normal" | "detailed"
    temperature: Optional[float] = None    # 0.0 – 1.0, clamped server-side
    # Multi-model routing: explicit role override. When set, this wins over
    # the auto-routed role derived from intent.
    role: Optional[str] = None  # "brain" | "coder" | "fast" | "vision"
    # Client-provided clipboard text. Sent by the v2 UI when the user asks
    # "what's in my clipboard" — the browser's Clipboard API reads the host
    # clipboard (with one-time permission) and forwards the content so that
    # macros like read_clipboard work even when Hunt runs inside a Linux
    # container that can't see the Windows desktop. Optional everywhere else.
    client_clipboard: Optional[str] = None
    # Phase 1 — Information Retrieval Agent. When True, /chat/stream runs the
    # tool-use planner: the LLM decides whether to call retrieve_memory (and
    # in the future, gmail_search / drive_search / etc.) before answering,
    # and the server feeds the tool results back into a streamed final reply.
    # When False (default), the existing pre-emptive context flow runs as-is.
    use_tools: bool = False


class ChatResponse(BaseModel):
    """Chat response model"""
    response: str
    intent: str
    timestamp: str
    tokens_used: Optional[int] = None


class MemoryRequest(BaseModel):
    """Memory request model"""
    content: str
    memory_type: str = "general"
    metadata: Optional[dict] = None


class CommandRequest(BaseModel):
    """Command request model"""
    command: str
    parameters: Optional[dict] = None


class ModelSwitchRequest(BaseModel):
    """Switch the active LLM model at runtime."""
    model: str


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    llm_connected: bool
    memory_available: bool
    timestamp: str


class TranscriptionResponse(BaseModel):
    """Voice transcription response"""
    transcript: str
    timestamp: str
    reason: str = "ok"
    diagnostics: Optional[dict] = None
    hint: Optional[str] = None


class VoiceDevicesResponse(BaseModel):
    """Voice device listing response"""
    devices: List[dict]
    default: Optional[dict] = None
    current: Optional[str] = None


# Global instances
llm_engine = None
context_manager = None
reasoning_engine = None
memory_store = None
conversation_memory = None
stt_engine = None
model_router = None
profile_store = None
action_history = None
action_approvals = None
project_store = None
briefing_composer: Optional[BriefingComposer] = None

# In-memory map of action proposals awaiting a user decision. Keyed by the
# proposal id emitted in the `action_proposal` stream event. Cleared on
# decide/timeout — bounded by len(_pending_action_proposals) <= ~50 in normal
# use, so a plain dict is fine.
_pending_action_proposals: dict = {}
# Same for Phase C fact proposals.
_pending_fact_proposals: dict = {}


def initialize_jarvis():
    """Initialize all Jarvis components"""
    global llm_engine, context_manager, reasoning_engine, memory_store, conversation_memory, stt_engine, model_router, profile_store, action_history, action_approvals, project_store, briefing_composer
    
    logger.info("Initializing Jarvis...")
    
    try:
        # Initialize core components
        llm_engine = get_llm_engine()
        context_manager = get_context_manager()
        reasoning_engine = get_reasoning()
        memory_store = get_memory_store()
        session_path = os.path.join(settings.SESSIONS_DIR, settings.SESSION_FILE)
        conversation_memory = ConversationMemory(
            memory_store,
            llm_engine=llm_engine,
            persist_path=session_path,
        )
        stt_engine = get_stt(
            settings.SPEECH_TO_TEXT_MODEL,
            settings.STT_LANGUAGE,
            settings.AUDIO_INPUT_DEVICE,
        )
        model_router = get_model_router(llm_engine)
        profile_store = get_profile_store(
            os.path.join(settings.DATA_DIR, "profile.json")
        )
        action_history = get_action_history(
            os.path.join(settings.DATA_DIR, "actions_history.json")
        )
        action_approvals = get_approval_store(
            "actions", root_dir=os.path.join(settings.DATA_DIR, "approvals")
        )
        project_store = get_project_store(
            os.path.join(settings.DATA_DIR, "projects.json")
        )
        # Phase D: Mongo cloud sync. Disabled unless MONGO_ENABLED=True AND
        # MONGO_URI is set. Failure to connect is non-fatal — Hunt keeps
        # running on local files only and logs the reason.
        if settings.MONGO_ENABLED and settings.MONGO_URI:
            mongo = get_mongo_sync(
                uri=settings.MONGO_URI,
                db_name=settings.MONGO_DB,
                device_id=settings.MONGO_DEVICE_ID,
            )
            if mongo and mongo.available:
                logger.info(
                    f"✓ MongoDB sync active (db='{settings.MONGO_DB}', "
                    f"device='{settings.MONGO_DEVICE_ID}')"
                )
            else:
                logger.warning("MongoDB sync enabled but connection failed; running local-only")
        # Briefing composer — pure read aggregator, no internal state. Built
        # AFTER Mongo so cloud_sync status reads correctly; tolerates None.
        briefing_composer = BriefingComposer(
            profile_store=profile_store,
            project_store=project_store,
            action_history=action_history,
            conversation_memory=conversation_memory,
            mongo_sync=mongo_sync_singleton(),
        )
        logger.info(
            f"✓ Hunt initialized. Active role map: {model_router.get_roles()}, "
            f"routing_enabled={settings.LLM_ROUTING_ENABLED}, "
            f"profile_set={sum(1 for v in profile_store.get().values() if v)}/{len(PROFILE_FIELDS)}, "
            f"action_policies={action_approvals.all()}"
        )
        return True
    except Exception as e:
        logger.error(f"Error initializing Jarvis: {str(e)}")
        return False


def should_use_live_search(message: str) -> bool:
    """Detect questions that benefit from current or company-specific context."""
    lowered = message.lower()
    live_terms = {
        "company",
        "companies",
        "stock",
        "revenue",
        "ceo",
        "founder",
        "funding",
        "valuation",
        "market",
        "latest",
        "current",
        "today",
        "news",
        "data",
        "scenario",
        "competitor",
        "competitors",
        "financial",
        "earnings",
    }
    return any(term in lowered for term in live_terms)


def search_web(query: str, limit: int = 3) -> List[dict]:
    """Fetch lightweight web-search snippets for grounding current answers."""
    if not settings.WEB_SEARCH_ENABLED:
        return []

    try:
        import requests

        response = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Web search failed: {str(e)}")
        return []

    results = []
    pattern = re.compile(
        r'<a rel="nofollow" class="result__a" href="(?P<url>.*?)".*?>(?P<title>.*?)</a>.*?'
        r'<a class="result__snippet".*?>(?P<snippet>.*?)</a>',
        re.DOTALL,
    )
    for match in pattern.finditer(response.text):
        title = clean_html(match.group("title"))
        snippet = clean_html(match.group("snippet"))
        url = unescape(match.group("url"))
        if title and snippet:
            results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= limit:
            break

    return results


def clean_html(value: str) -> str:
    """Remove simple HTML tags/entities from search result text."""
    value = re.sub(r"<.*?>", "", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def build_live_context(results: List[dict]) -> str:
    """Build LLM context from search snippets."""
    if not results:
        return (
            "Live data search was attempted but no usable results were found. "
            "If the question needs current facts, say you do not have enough verified data."
        )

    lines = [
        "Live web context for current/company data. Use this context, cite source titles or URLs, "
        "and do not invent facts that are not present here."
    ]
    for index, result in enumerate(results, 1):
        lines.append(
            f"{index}. {result['title']}\n"
            f"URL: {result['url']}\n"
            f"Snippet: {result['snippet']}"
        )
    return "\n\n".join(lines)


# Routes
@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    initialize_jarvis()


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint.

    `llm_connected` reflects whether at least one configured LLM backend is
    reachable. The legacy implementation only pinged Ollama, which meant the
    pill went red for users routing entirely to OpenRouter even though chat
    actually worked. New rule:

      - If OPENROUTER_API_KEY is set, treat OpenRouter as the primary
        backend and report online without an explicit ping (avoids hammering
        the OpenRouter API every poll; chat errors will surface naturally
        if the key is bad or out of credit).
      - Otherwise fall back to the Ollama probe.
    """
    try:
        import os
        if os.environ.get("OPENROUTER_API_KEY"):
            llm_ok = True
        elif llm_engine:
            llm_ok = llm_engine.check_connection()
        else:
            llm_ok = False
        memory_ok = memory_store is not None

        return HealthResponse(
            status="running",
            llm_connected=llm_ok,
            memory_available=memory_ok,
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):  # sync: blocking LLM call runs in FastAPI's threadpool, not the event loop
    """
    Chat endpoint - Main conversation interface
    
    Args:
        request: Chat request with message
        
    Returns:
        Chat response with AI response
    """
    try:
        if not llm_engine:
            raise HTTPException(status_code=503, detail="LLM not initialized")
        
        # Add to context
        context_manager.add_to_context("last_user_input", request.message)
        
        # Analyze intent
        intent_analysis = reasoning_engine.analyze_intent(request.message)
        intent = intent_analysis["primary_intent"]
        # Phase E1: "continue my X" / "switch to project X" — look up the
        # project by fuzzy name match and activate it on the session. Chip-
        # less because the user just asked to switch context, not perform
        # a destructive action.
        _maybe_activate_project(intent, request.message)

        # Phase F: macro short-circuit (mirror of /chat/stream). Skip the LLM
        # entirely, run the recipe, and return its script as the response.
        if is_macro(intent):
            macro_ctx = _macro_ctx()
            if getattr(request, "client_clipboard", None):
                macro_ctx["client_clipboard"] = request.client_clipboard
            result = run_macro(intent, macro_ctx)
            try:
                conversation_memory.add_exchange(request.message, result.script)
            except Exception as e:
                logger.warning(f"Macro post-persistence failed (non-fatal): {e}")
            payload = ChatResponse(
                response=result.script,
                intent=intent,
                timestamp=datetime.now().isoformat(),
            ).dict()
            payload["macro_data"] = {
                "macro": result.name,
                "structured": result.structured,
                "side_effects": result.side_effects,
                "failed": result.failed,
            }
            return payload
        
        context, session_history, effective_role = _build_chat_context_and_history(request, intent)

        # Pick the model for this turn — intent-driven, or honor an explicit
        # role override from the request. Voice replies always use the fast role.
        chosen_model = model_router.pick_model(
            intent=intent,
            voice_mode=request.voice_mode,
            explicit_role=request.role,
        ) if model_router else None

        # Generate response
        response = llm_engine.generate(
            request.message,
            context,
            voice_mode=request.voice_mode,
            history=session_history,
            response_length=request.response_length,
            temperature=request.temperature,
            model_override=chosen_model,
            role=effective_role,
        )
        
        # Store in memory (this may trigger summary compaction once the verbatim
        # buffer exceeds MAX_VERBATIM_EXCHANGES).
        conversation_memory.add_exchange(request.message, response)
        # Phase C: fact candidates are NO LONGER auto-saved here. They're
        # surfaced via `action_event` / `fact_events` on the response payload
        # so the UI can render approval chips. See `_maybe_fact_events` below.

        # Store additional memory if specified
        if request.memory_type:
            memory_store.store_memory(
                content=f"Q: {request.message}\nA: {response}",
                memory_type=request.memory_type
            )
        
        # Phase B + C: action proposal AND fact candidates piggyback on the
        # non-streaming response so the UI can render chips even with stream off.
        proposal_events = list(_maybe_action_events(intent, request.message))
        fact_events = list(_maybe_fact_events(request.message))

        payload = ChatResponse(
            response=response,
            intent=intent,
            timestamp=datetime.now().isoformat()
        ).dict()
        if proposal_events:
            payload["action_event"] = _json.loads(proposal_events[0])
        if fact_events:
            payload["fact_events"] = [_json.loads(e) for e in fact_events]
        return payload
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def _effective_role(request: "ChatRequest", intent: str) -> str:
    """Resolve the role this turn will use — same logic ModelRouter uses,
    but lifted out so we can pick the system prompt + trim context to match."""
    if request.role:
        return request.role
    if request.voice_mode:
        return "fast"
    # Local import keeps the module-load order resilient.
    from brain import INTENT_TO_ROLE
    return INTENT_TO_ROLE.get((intent or "").lower(), "fast")


def _macro_event_stream(intent: str, request: "ChatRequest"):
    """Generator yielding NDJSON events for a macro short-circuit.

    Shape mirrors the regular /chat/stream so the UI can treat it identically:
      {type:"meta",       intent, model:"macro", role:"macro", macro:true}
      {type:"macro_data", macro, structured}            ← NEW (UI renders card)
      {type:"token",      content}  (repeated, ~10 chars at a time)
      {type:"done",       response, intent}             ← also persists turn
      {type:"error",      message}                       (on macro failure)
    """
    yield _json.dumps({
        "type": "meta",
        "intent": intent,
        "model": "macro",
        "role": "macro",
        "macro": True,
    }) + "\n"

    # Inject client-provided context so the read_clipboard macro can use the
    # browser-supplied clipboard text instead of probing the container's
    # (empty) Linux clipboard.
    macro_ctx = _macro_ctx()
    if getattr(request, "client_clipboard", None):
        macro_ctx["client_clipboard"] = request.client_clipboard
    result = run_macro(intent, macro_ctx)

    yield _json.dumps({
        "type": "macro_data",
        "macro": result.name,
        "structured": result.structured,
        "side_effects": result.side_effects,
    }) + "\n"

    if result.failed:
        yield _json.dumps({
            "type": "error",
            "message": result.error or "Macro failed.",
        }) + "\n"
        return

    # Stream the script in small chunks so the UI's existing incremental TTS
    # picks it up sentence by sentence — exactly the same code path as LLM
    # tokens. ~10-char chunks land somewhere between "instant" and "chatty".
    script = result.script or ""
    CHUNK = 16
    for i in range(0, len(script), CHUNK):
        yield _json.dumps({"type": "token", "content": script[i : i + CHUNK]}) + "\n"

    # Persist the macro turn so the chat memory and Mongo session reflect it,
    # then emit done. The user said the words, Hunt produced the script —
    # both belong in the verbatim window like any other turn.
    try:
        conversation_memory.add_exchange(request.message, script)
    except Exception as e:
        logger.warning(f"Macro post-persistence failed (non-fatal): {e}")

    yield _json.dumps({
        "type": "done",
        "response": script,
        "intent": intent,
    }) + "\n"


def _macro_ctx() -> dict:
    """Bundle every singleton a macro might need. Each macro consumes only
    the keys it cares about; new macros can add new keys here without
    touching the dispatch sites in /chat and /chat/stream."""
    return {
        "briefing_composer": briefing_composer,
        "project_store": project_store,
        "conversation_memory": conversation_memory,
        "llm_engine": llm_engine,
        "memory_store": memory_store,
        "action_history": action_history,
        "profile_store": profile_store,
    }


@app.get("/briefing")
async def get_briefing():
    """Return the current briefing payload (no TTS, no chat side effects).
    Useful for a 'Brief me' UI button and for debugging the composer."""
    if not briefing_composer:
        raise HTTPException(status_code=503, detail="Briefing composer not initialized")
    try:
        payload = briefing_composer.compose()
        return payload.to_dict()
    except Exception as e:
        logger.error(f"Briefing compose error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _build_chat_context_and_history(request: "ChatRequest", intent: str = ""):
    """Shared context+history build used by both /chat and /chat/stream.

    Coding turns get a *thin* context: skip profile facts, rolling summary,
    semantic memory, and live search. Only the last 4 verbatim turns are sent.
    This stops travel-planner / project chatter from drowning the coder model
    in irrelevant noise, and keeps the small model focused on the actual task.
    """
    role = _effective_role(request, intent)
    is_coder = role == "coder"

    context_parts: List[str] = []
    if request.include_context and not is_coder:
        # 0. Active project (Phase E1) — what is the user currently focused on?
        # Injected first because every other context block should be read in
        # the project's frame.
        if project_store and conversation_memory and conversation_memory.active_project_id:
            proj_block = project_store.format_active_block(
                conversation_memory.active_project_id
            )
            if proj_block:
                context_parts.append(proj_block)

        # 1. Structured profile (the user themselves).
        if profile_store:
            profile_block = profile_store.format_for_prompt()
            if profile_block:
                context_parts.append(profile_block)

        # 2. Loose extracted facts come AFTER the profile so the profile wins
        # when the same info appears in both.
        profile_facts = memory_store.get_profile_facts(limit=30)
        if profile_facts:
            context_parts.append(
                "Other remembered facts about the user (apply when relevant):\n- " +
                "\n- ".join(profile_facts)
            )
        running_summary = conversation_memory.get_running_summary()
        if running_summary:
            context_parts.append(
                "Summary of earlier turns in THIS chat (already happened — do not "
                "contradict; refer to it if asked about what was discussed):\n" + running_summary
            )
        # Multi-source retrieval: searches conversation + project + task +
        # document chunks together via hybrid (vector + BM25) + reranker.
        # Falls back to conversation-only `get_context` if the multi-source
        # method isn't available on an older snapshot.
        try:
            multi_ctx = conversation_memory.get_multi_source_context(request.message, limit=5)
        except AttributeError:
            multi_ctx = conversation_memory.get_context(request.message, limit=3)
        if multi_ctx:
            context_parts.append(multi_ctx)
    if request.use_live_search and should_use_live_search(request.message) and not is_coder:
        live_results = search_web(request.message, limit=settings.WEB_SEARCH_RESULTS)
        context_parts.append(build_live_context(live_results))

    context = "\n\n".join(context_parts) or None
    # Coder history is tight (4 turns) so "explain it / optimize it" follow-ups
    # still see the previous code, but unrelated turns don't leak in.
    history_limit = 4 if is_coder else 10
    history = conversation_memory.get_session_messages(limit=history_limit)
    return context, history, role


@app.post("/chat/stream")
def chat_stream(request: ChatRequest):  # sync: preamble + sync stream generator run off the event loop
    """Stream the assistant response token-by-token via NDJSON.

    Each line is one JSON object:
      {"type":"meta","intent":"..."}             — sent once, immediately
      {"type":"token","content":"..."}           — repeated, partial tokens
      {"type":"done","response":"...full..."}    — sent once, full reply text
      {"type":"error","message":"..."}           — sent on failure (then stream ends)

    The browser updates the assistant bubble incrementally and, on "done",
    runs TTS on the full text.
    """
    if not llm_engine:
        raise HTTPException(status_code=503, detail="LLM not initialized")

    context_manager.add_to_context("last_user_input", request.message)
    intent_analysis = reasoning_engine.analyze_intent(request.message)
    intent = intent_analysis["primary_intent"]
    _maybe_activate_project(intent, request.message)

    # Macro short-circuit (Phase F): if the intent is a deterministic recipe,
    # skip the LLM and stream the macro's pre-composed script as if it were
    # a regular reply. The structured payload is also emitted so the UI can
    # render a card alongside the spoken script.
    if is_macro(intent):
        return StreamingResponse(
            _macro_event_stream(intent, request),
            media_type="application/x-ndjson",
        )

    context, history, effective_role = _build_chat_context_and_history(request, intent)
    chosen_model = model_router.pick_model(
        intent=intent,
        voice_mode=request.voice_mode,
        explicit_role=request.role,
    ) if model_router else None

    def event_stream():
        # Response Composer V2 — depth-aware contract injected into the
        # system prompt, dual-output (display + voice) extracted after.
        from brain import classify_depth, build_contract, extract_voice_and_display

        depth = classify_depth(intent, request.message)
        contract = build_contract(depth, request.voice_mode)

        # The contract goes into the context string so it lands as an extra
        # system message alongside the long-term memory block. This is the
        # minimum-touch way to bias the model without changing llm_engine.
        effective_context = context or ""
        if contract:
            if effective_context:
                effective_context = effective_context + "\n\n" + contract
            else:
                effective_context = contract

        # `role` is now included in the meta event so the UI can render code
        # cards + suggestion chips without re-deriving it. `depth` is included
        # so the UI can adjust pacing / chip rendering by query weight.
        yield _json.dumps({
            "type": "meta",
            "intent": intent,
            "model": chosen_model,
            "role": effective_role,
            "depth": depth,
        }) + "\n"

        # Stream the LLM tokens raw. The [VOICE]: marker, if present, will
        # arrive as part of the stream — we'll strip it at done-time before
        # the UI renders the final text.
        collected: List[str] = []
        # Phase 1 — when research mode is on, route through the tool-using
        # planner. It yields the same shape of events plus two new ones
        # (tool_call, tool_result) that we forward to the client so the UI
        # can show a "Searching memory..." indicator + citation cards.
        #
        # Polish 1.5 gate: even with research mode ON, trivial queries
        # (greetings, pure computation, generic knowledge questions like
        # "what is bubble sort") skip the tool flow. Saves a wasted LLM
        # call per turn AND avoids the planner's "I must call a tool"
        # behavior firing on questions where no retrieval makes sense.
        # The user opted into research mode to ground recall answers, not
        # to burn a second call on arithmetic.
        from brain import is_recall_query
        citations: List[Dict[str, Any]] = []
        use_tools_effective = request.use_tools and is_recall_query(request.message)
        if use_tools_effective:
            stream_iter = llm_engine.chat_with_tools_stream(
                request.message,
                effective_context,
                voice_mode=request.voice_mode,
                history=history,
                response_length=request.response_length,
                temperature=request.temperature,
                model_override=chosen_model,
                role=effective_role,
            )
        else:
            stream_iter = llm_engine.generate_stream(
                request.message,
                effective_context,
                voice_mode=request.voice_mode,
                history=history,
                response_length=request.response_length,
                temperature=request.temperature,
                model_override=chosen_model,
                role=effective_role,
            )
        try:
            for event_type, payload in stream_iter:
                if event_type == "tool_call":
                    # Forward to the UI so it can show "Searching memory…".
                    yield _json.dumps({
                        "type": "tool_call",
                        "name": payload.get("name"),
                        "arguments": payload.get("arguments"),
                        "label": payload.get("label"),
                    }) + "\n"
                    continue
                if event_type == "tool_result":
                    # Stash the snippets as citations the UI will render
                    # under the final answer.
                    result = payload.get("result") or {}
                    for snip in (result.get("results") or []):
                        citations.append(snip)
                    yield _json.dumps({
                        "type": "tool_result",
                        "name": payload.get("name"),
                        "count": payload.get("count"),
                    }) + "\n"
                    continue
                if event_type == "token":
                    collected.append(payload)
                    yield _json.dumps({"type": "token", "content": payload}) + "\n"
                elif event_type == "done":
                    # Use the streamer's cleaned full text when present, fall back
                    # to the concatenation of tokens (also cleaned of <think> tags).
                    llm_text = (payload or "".join(collected)).strip()

                    # Split into display (what's shown) and voice (what's spoken).
                    # In voice mode the contract was empty, so the whole thing is
                    # the voice answer and display matches.
                    if request.voice_mode:
                        display_text = llm_text
                        voice_text = llm_text
                    else:
                        display_text, voice_text = extract_voice_and_display(llm_text)

                    # Emit the speakable line as its own event so the client can
                    # feed it to TTS without parsing markdown out of the visible text.
                    if voice_text:
                        yield _json.dumps({"type": "voice", "content": voice_text}) + "\n"

                    full = display_text
                    try:
                        conversation_memory.add_exchange(request.message, full)
                    except Exception as e:
                        logger.warning(f"Post-stream persistence failed: {e}")
                    yield _json.dumps({
                        "type": "done",
                        "response": full,
                        "voice_response": voice_text,
                        "intent": intent,
                        "depth": depth,
                        # Phase 1: citations from any retrieve_memory calls
                        # made during this turn. Empty list when not in
                        # research mode or when no tools fired.
                        "citations": citations,
                    }) + "\n"
                    # Phase B: emit action proposal AFTER `done` so the chip
                    # renders below the assistant reply. The proposer is a
                    # cheap regex — no LLM call.
                    for proposal_event in _maybe_action_events(intent, request.message):
                        yield proposal_event
                    # Phase C: same for fact candidates. extract_facts no longer
                    # auto-saves — chips decide save/skip/always/promote.
                    for fact_event in _maybe_fact_events(request.message):
                        yield fact_event
                    return
                elif event_type == "error":
                    yield _json.dumps({"type": "error", "message": payload}) + "\n"
                    return
        except Exception as e:
            logger.error(f"Stream failure: {e}")
            yield _json.dumps({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/voice/transcribe", response_model=TranscriptionResponse)
async def transcribe_voice(audio: UploadFile = File(...)):
    """Transcribe browser-recorded microphone audio with local Whisper."""
    try:
        if not stt_engine:
            raise HTTPException(status_code=503, detail="Speech-to-text is not initialized")

        audio_bytes = await audio.read()
        logger.info(
            f"Received audio upload: {len(audio_bytes)} bytes, filename: {audio.filename}, "
            f"content_type: {audio.content_type}"
        )

        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received")

        # Whisper.transcribe is synchronous and CPU-heavy (~1-3s on tiny
        # model). Calling it directly inside an async endpoint blocks the
        # entire event loop, which means /health, /chat/stream and every
        # other request stalls until transcription finishes. On Windows
        # this also tends to leave the loop in a degraded state. Punt to
        # a threadpool so the loop stays responsive.
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(stt_engine.transcribe_bytes_with_diagnostics, audio_bytes)
        transcript = result.get("transcript", "")
        reason = result.get("reason", "ok")
        diagnostics = result.get("diagnostics") or {}

        hint = None
        if not transcript:
            peak = float(diagnostics.get("peak", 0.0))
            duration = float(diagnostics.get("duration_seconds", 0.0))
            if reason == "silent" or peak < 0.01:
                hint = (
                    f"Mic audio was very quiet (peak {peak:.3f}). Speak closer to the mic, "
                    "raise input volume in Windows settings, or pick a different mic."
                )
            elif duration < 0.6:
                hint = (
                    f"Recording was only {duration:.2f}s long. Hold the voice button longer "
                    "or click again to stop after you finish talking."
                )
            elif reason == "low_confidence":
                hint = "Audio was captured but Whisper was not confident. Try speaking more clearly."
            elif reason == "decode_failed":
                hint = "Audio captured but Whisper produced no text. Check mic quality or try again."
            else:
                hint = "No transcript was produced. Check the mic and try again."

        logger.info(f"Transcription result: '{transcript}' reason={reason} diagnostics={diagnostics}")

        return TranscriptionResponse(
            transcript=transcript,
            timestamp=datetime.now().isoformat(),
            reason=reason,
            diagnostics=diagnostics,
            hint=hint,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice transcription error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voice/devices", response_model=VoiceDevicesResponse)
async def list_voice_devices():
    """List available microphone input devices for diagnostics."""
    try:
        if not stt_engine:
            raise HTTPException(status_code=503, detail="Speech-to-text is not initialized")

        devices = stt_engine.list_input_devices()
        default = stt_engine.get_default_input_device()
        current = None
        if stt_engine.input_device is not None:
            current = str(stt_engine.input_device)

        return VoiceDevicesResponse(devices=devices, default=default, current=current)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice device listing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/store")
async def store_memory(request: MemoryRequest):
    """Store information in memory"""
    try:
        if not memory_store:
            raise HTTPException(status_code=503, detail="Memory not initialized")
        
        memory_id = memory_store.store_memory(
            content=request.content,
            memory_type=request.memory_type,
            metadata=request.metadata
        )
        
        return {"memory_id": memory_id, "status": "stored"}
    except Exception as e:
        logger.error(f"Memory storage error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/retrieve")
async def retrieve_memories(query: str, limit: int = 3, types: Optional[str] = None):
    """Retrieve memories using the advanced RAG pipeline.

    Query params:
        query — required user query string
        limit — top K to return (default 3)
        types — comma-separated memory types to search (e.g. "conversation,document").
                Default: all configured types from MEMORY_SEARCH_TYPES.
    """
    try:
        if not memory_store:
            raise HTTPException(status_code=503, detail="Memory not initialized")

        type_list = None
        if types:
            type_list = [t.strip() for t in types.split(",") if t.strip()]

        try:
            memories = memory_store.retrieve(query=query, limit=limit, types=type_list)
        except AttributeError:
            # Fallback for the old single-type API.
            memories = memory_store.retrieve_memories(query, limit)
        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        logger.error(f"Memory retrieval error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================== #
# Document RAG endpoints — upload, list, delete, rebuild-index.
# ============================================================== #

@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
):
    """Ingest a document into Hunt's RAG index.

    Accepts: txt, md, source code, pdf. The file is saved under
    DOCUMENT_STORE_DIR, then chunked and indexed in Chroma as
    memory_type="document". Subsequent chats can retrieve it via the
    multi-source context builder.
    """
    try:
        from memory.document_ingestor import ingest_file

        # Save the upload to the persistent document directory.
        from config.settings import settings as _settings
        store_dir = getattr(_settings, "DOCUMENT_STORE_DIR", "./data/documents")
        os.makedirs(store_dir, exist_ok=True)
        safe_name = os.path.basename(file.filename or "upload")
        # Prefix with a short uuid so two files with the same name coexist.
        prefix = uuid.uuid4().hex[:8]
        dest_path = os.path.join(store_dir, f"{prefix}_{safe_name}")

        content = await file.read()
        with open(dest_path, "wb") as fh:
            fh.write(content)

        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(ingest_file, dest_path, title, None)
        if result.get("error"):
            # Remove the saved file when ingestion failed cleanly so we
            # don't leave orphan bytes on disk.
            try:
                os.remove(dest_path)
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=result["error"])

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document upload error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/documents")
async def list_documents_endpoint():
    """Return the document manifest (one entry per ingested file)."""
    try:
        from memory.document_ingestor import list_documents
        return {"documents": list_documents()}
    except Exception as e:
        logger.error(f"Document list error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/documents/{doc_id}")
async def delete_document_endpoint(doc_id: str):
    """Drop all chunks + manifest entry for a previously-uploaded document."""
    try:
        from memory.document_ingestor import delete_document, get_document
        meta = get_document(doc_id)
        n = delete_document(doc_id)
        # Best-effort remove the persisted file too.
        if meta and meta.get("source") and os.path.exists(meta["source"]):
            try:
                os.remove(meta["source"])
            except Exception:
                pass
        return {"doc_id": doc_id, "chunks_removed": n}
    except Exception as e:
        logger.error(f"Document delete error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/rebuild-index")
async def rebuild_memory_index():
    """Backfill the vector index with all projects + tasks.

    Useful when upgrading from a pre-RAG snapshot, or after restoring from
    backup. Conversations are auto-indexed on each turn so they don't need
    rebuilding; documents have their own upload flow.
    """
    try:
        if not memory_store or not project_store:
            raise HTTPException(status_code=503, detail="Memory not initialized")
        projects = project_store.list(include_archived=True)
        n = 0
        for proj in projects:
            try:
                memory_store.index_project(proj)
                n += 1
            except Exception as e:
                logger.warning(f"Re-index of project {proj.get('id')} failed: {e}")
        return {"projects_indexed": n, "documents_skipped": "use /documents/upload to (re)ingest"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Rebuild index error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/profile")
async def list_profile_facts():
    """List all stored profile facts with their IDs (for the UI memory panel)."""
    try:
        if not memory_store:
            raise HTTPException(status_code=503, detail="Memory not initialized")
        items = memory_store.get_all_memories(memory_type="profile")
        # Newest first by stored timestamp.
        items.sort(
            key=lambda it: (it.get("metadata") or {}).get("timestamp", ""),
            reverse=True,
        )
        facts = [
            {
                "id": it.get("id", ""),
                "content": (it.get("content") or "").strip(),
                "timestamp": (it.get("metadata") or {}).get("timestamp", ""),
            }
            for it in items
            if it.get("content")
        ]
        return {"facts": facts, "count": len(facts)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile list error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memory/profile")
async def wipe_profile_facts():
    """Delete every stored profile fact. Used by the 'Wipe profile facts' button."""
    try:
        if not memory_store:
            raise HTTPException(status_code=503, detail="Memory not initialized")
        items = memory_store.get_all_memories(memory_type="profile")
        deleted = 0
        for item in items:
            fid = item.get("id")
            if fid and memory_store.delete_memory(fid):
                deleted += 1
        return {"status": "ok", "deleted": deleted}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile wipe error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memory/profile/{fact_id}")
async def delete_profile_fact(fact_id: str):
    """Delete a single profile fact by id."""
    try:
        if not memory_store:
            raise HTTPException(status_code=503, detail="Memory not initialized")
        ok = memory_store.delete_memory(fact_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Fact not found")
        return {"status": "deleted", "id": fact_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile delete error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class ProfileUpdateRequest(BaseModel):
    """Sparse profile update. Any field not in the body is left untouched."""
    name: Optional[str] = None
    occupation: Optional[str] = None
    projects: Optional[str] = None
    interests: Optional[str] = None
    preferred_tone: Optional[str] = None
    daily_schedule: Optional[str] = None
    frequent_contacts: Optional[str] = None
    goals: Optional[str] = None


@app.get("/profile")
async def get_profile():
    """Return the current structured user profile (all 8 fields, possibly empty)."""
    try:
        if not profile_store:
            raise HTTPException(status_code=503, detail="Profile store not initialized")
        return {"profile": profile_store.get(), "fields": list(PROFILE_FIELDS)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get profile error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/profile")
async def replace_profile(request: ProfileUpdateRequest):
    """Replace the entire profile. Unspecified fields are cleared to empty."""
    try:
        if not profile_store:
            raise HTTPException(status_code=503, detail="Profile store not initialized")
        return {"profile": profile_store.replace(request.dict())}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Replace profile error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/profile")
async def patch_profile(request: ProfileUpdateRequest):
    """Sparse update — only fields present (non-null) in the body are changed."""
    try:
        if not profile_store:
            raise HTTPException(status_code=503, detail="Profile store not initialized")
        # exclude_none so only explicitly-sent fields land in the patch dict.
        return {"profile": profile_store.patch(request.dict(exclude_none=True))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Patch profile error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/profile")
async def clear_profile():
    """Wipe the entire structured profile."""
    try:
        if not profile_store:
            raise HTTPException(status_code=503, detail="Profile store not initialized")
        return {"profile": profile_store.clear(), "status": "cleared"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Clear profile error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/session")
async def get_session_state():
    """Return the current running summary, verbatim turn count, and the actual
    last-N user/assistant exchanges so the Memory panel can render them."""
    try:
        if not conversation_memory:
            raise HTTPException(status_code=503, detail="Conversation memory not initialized")
        # Surface verbatim turns so the UI shows what Hunt actually remembers
        # in this session, not just a "0/10" counter.
        recent = []
        for turn in conversation_memory.current_session:
            recent.append({
                "user": (turn.get("user") or "").strip(),
                "assistant": (turn.get("assistant") or "").strip(),
            })
        return {
            "rolling_summary": conversation_memory.get_running_summary(),
            "verbatim_count": len(conversation_memory.current_session),
            "verbatim_max": conversation_memory.MAX_VERBATIM_EXCHANGES,
            "recent": recent,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session state error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/command")
async def execute_command(request: CommandRequest):
    """Execute system command"""
    try:
        cmd = request.command.lower()
        params = request.parameters or {}
        
        # Desktop automation commands
        if cmd == "open_app":
            desktop = get_desktop_automation()
            app_name = params.get("app_name", "")
            success = desktop.open_application(app_name)
            return {"status": "success" if success else "failed", "command": cmd}
        
        elif cmd == "close_app":
            desktop = get_desktop_automation()
            app_name = params.get("app_name", "")
            success = desktop.close_application(app_name)
            return {"status": "success" if success else "failed", "command": cmd}
        
        # Browser commands
        elif cmd == "open_browser":
            browser = get_browser_automation()
            url = params.get("url", "")
            success = browser.open_browser(url)
            return {"status": "success" if success else "failed", "command": cmd}
        
        elif cmd == "search":
            browser = get_browser_automation()
            query = params.get("query", "")
            success = browser.search(query)
            return {"status": "success" if success else "failed", "command": cmd}
        
        else:
            raise HTTPException(status_code=400, detail=f"Unknown command: {cmd}")
    
    except Exception as e:
        logger.error(f"Command execution error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/conversation/export")
async def export_conversation(format: str = "md"):
    """Return the running session as markdown (default) or JSON.

    Markdown includes the rolling summary above the verbatim turns, so users
    get the *full* thread even when the older parts have been compacted.
    """
    try:
        if not conversation_memory:
            raise HTTPException(status_code=503, detail="Conversation memory not initialized")

        summary = conversation_memory.get_running_summary()
        recent = list(conversation_memory.current_session)
        exported_at = datetime.now().isoformat(timespec="seconds")

        if format == "json":
            return {
                "exported_at": exported_at,
                "rolling_summary": summary,
                "recent": recent,
            }

        lines = [
            "# Hunt conversation transcript",
            f"_Exported: {exported_at}_",
            "",
        ]
        if summary:
            lines.append("## Summary of earlier turns")
            lines.append("")
            lines.append(summary)
            lines.append("")
        if recent:
            lines.append("## Recent exchanges")
            lines.append("")
            for i, turn in enumerate(recent, 1):
                user = (turn.get("user") or "").strip()
                assistant = (turn.get("assistant") or "").strip()
                lines.append(f"### Turn {i}")
                lines.append("")
                if user:
                    lines.append(f"**You:** {user}")
                    lines.append("")
                if assistant:
                    lines.append(f"**Hunt:** {assistant}")
                    lines.append("")
        if not summary and not recent:
            lines.append("_No conversation yet._")

        md = "\n".join(lines).rstrip() + "\n"
        return Response(content=md, media_type="text/markdown; charset=utf-8")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Phase B: action proposals + execution ----------

def _maybe_action_events(intent: str, message: str):
    """Yield NDJSON-encoded stream events for an action proposal, if any.

    Three branches based on the user's saved policy for the action type:
      - "always" → execute immediately, yield one `action_executed` event
      - "ask"    → save proposal, yield one `action_proposal` event with chips
      - "never"  → silently skip (no event)
    """
    if not action_history or not action_approvals:
        return
    if (intent or "").lower() not in ACTION_INTENTS:
        return
    proposal = propose_action(intent, message)
    if not proposal:
        return

    policy = proposal["policy"]
    action = proposal["action"]
    params = proposal["params"]

    if policy == "never":
        return

    if policy == "always":
        result = run_action(action, params)
        entry = action_history.record(action, params, "always", result)
        yield _json.dumps({
            "type": "action_executed",
            "action": action,
            "params": params,
            "result": result,
            "history_id": entry["id"],
            "auto": True,
        }) + "\n"
        return

    # policy == "ask": stash and emit a chip request.
    _pending_action_proposals[proposal["id"]] = {
        "action": action,
        "params": params,
        "prompt": proposal["prompt"],
    }
    yield _json.dumps({
        "type": "action_proposal",
        "id": proposal["id"],
        "action": action,
        "params": params,
        "prompt": proposal["prompt"],
        "options": [
            {"label": "Allow", "value": "allow", "kind": "primary"},
            {"label": "Deny", "value": "deny", "kind": "neutral"},
            {"label": "Always allow", "value": "always", "kind": "subtle"},
        ],
    }) + "\n"


class ActionDecisionRequest(BaseModel):
    decision: str  # "allow" | "deny" | "always"


@app.post("/actions/{proposal_id}/decide")
async def decide_action(proposal_id: str, request: ActionDecisionRequest):
    """Act on the user's chip click for a pending action proposal."""
    try:
        if proposal_id not in _pending_action_proposals:
            raise HTTPException(status_code=404, detail="Unknown or expired proposal")
        choice = (request.decision or "").lower()
        if choice not in ("allow", "deny", "always"):
            raise HTTPException(status_code=400, detail="decision must be allow|deny|always")
        proposal = _pending_action_proposals.pop(proposal_id)
        action = proposal["action"]
        params = proposal["params"]

        if choice == "deny":
            entry = action_history.record(action, params, "deny", {"status": "denied", "detail": ""})
            return {"status": "denied", "history_id": entry["id"]}

        if choice == "always":
            action_approvals.set(action, "always")

        result = run_action(action, params)
        entry = action_history.record(action, params, choice, result)
        return {
            "status": result.get("status", "n/a"),
            "detail": result.get("detail", ""),
            "history_id": entry["id"],
            "policy_set": (choice == "always"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"decide_action error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/actions")
async def list_actions(limit: int = 20):
    """List recent action attempts (newest first) for the history view."""
    if not action_history:
        raise HTTPException(status_code=503, detail="Action history not initialized")
    return {
        "actions": action_history.list_recent(limit=limit),
        "registry": {k: v["label"] for k, v in ACTION_REGISTRY.items()},
        "pending_count": len(_pending_action_proposals),
    }


@app.delete("/actions")
async def clear_action_history():
    """Wipe the action history. Approval policies are untouched — use the
    /actions/policies endpoints to revoke an auto-approval."""
    if not action_history:
        raise HTTPException(status_code=503, detail="Action history not initialized")
    dropped = action_history.clear()
    return {"status": "ok", "dropped": dropped}


@app.get("/actions/policies")
async def list_action_policies():
    """Return current per-action auto-approval policies."""
    if not action_approvals:
        raise HTTPException(status_code=503, detail="Approval store not initialized")
    return {"policies": action_approvals.all(), "actions": list(ACTION_REGISTRY.keys())}


@app.delete("/actions/policies/{action_type}")
async def revoke_action_policy(action_type: str):
    """Revoke an 'always allow' rule for a specific action type."""
    if not action_approvals:
        raise HTTPException(status_code=503, detail="Approval store not initialized")
    removed = action_approvals.remove(action_type)
    return {"removed": removed, "action": action_type}


# ---------- Phase C: memory approval (chips for extracted facts) ----------

def _maybe_fact_events(message: str):
    """Yield NDJSON-encoded `fact_proposal` events for each candidate fact.

    Per-pattern policy (in ApprovalStore('fact_patterns')):
      - "always" → auto-save silently (no event)
      - "never"  → drop silently
      - "ask"    → stash + emit chip event
    """
    if not memory_store:
        return
    try:
        candidates = extract_facts(message)
    except Exception as e:
        logger.warning(f"extract_facts failed: {e}")
        return
    if not candidates:
        return
    fact_approvals = get_approval_store(
        "fact_patterns", root_dir=os.path.join(settings.DATA_DIR, "approvals")
    )
    for c in candidates:
        policy = fact_approvals.get(c["pattern"])
        if policy == "never":
            continue
        if policy == "always":
            try:
                memory_store.store_profile_fact(c["text"])
            except Exception as e:
                logger.warning(f"Auto-save fact failed: {e}")
            continue
        # policy == "ask"
        _pending_fact_proposals[c["id"]] = c
        yield _json.dumps({
            "type": "fact_proposal",
            "id": c["id"],
            "text": c["text"],
            "pattern": c["pattern"],
            "captured": c["captured"],
            "profile_field": c["profile_field"],
            "prompt": f"Save: {c['text']}",
            "options": [
                {"label": "Save", "value": "save", "kind": "primary"},
                {"label": "Skip", "value": "skip", "kind": "neutral"},
                {"label": "Always save these", "value": "always_save", "kind": "subtle"},
            ],
        }) + "\n"


class FactDecisionRequest(BaseModel):
    decision: str  # "save" | "skip" | "always_save" | "never_again" | "promote"
    profile_field: Optional[str] = None  # required when decision == "promote"


@app.post("/memory/facts/{fact_id}/decide")
async def decide_fact(fact_id: str, request: FactDecisionRequest):
    """Apply the user's chip click for a pending fact proposal.

    save         → store as a profile fact (current behavior)
    skip         → drop silently
    always_save  → set per-pattern "always" policy + save this one
    never_again  → set per-pattern "never" policy + drop this one
    promote      → write candidate.captured into the chosen profile field
                   (requires `profile_field` in the body)
    """
    try:
        if fact_id not in _pending_fact_proposals:
            raise HTTPException(status_code=404, detail="Unknown or expired fact")
        candidate = _pending_fact_proposals.pop(fact_id)
        choice = (request.decision or "").lower()

        if choice == "skip":
            return {"status": "skipped"}

        fact_approvals = get_approval_store(
            "fact_patterns", root_dir=os.path.join(settings.DATA_DIR, "approvals")
        )

        if choice == "always_save":
            fact_approvals.set(candidate["pattern"], "always")
            try:
                memory_store.store_profile_fact(candidate["text"])
                return {"status": "saved_and_remembered", "pattern": candidate["pattern"]}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        if choice == "never_again":
            fact_approvals.set(candidate["pattern"], "never")
            return {"status": "muted_pattern", "pattern": candidate["pattern"]}

        if choice == "promote":
            field = request.profile_field or candidate.get("profile_field")
            if field not in PROFILE_FIELDS:
                raise HTTPException(status_code=400, detail=f"Invalid profile_field: {field}")
            captured = (candidate.get("captured") or "").strip() or candidate["text"]
            current = (profile_store.get().get(field) or "").strip()
            # If the field already has content, append (comma-separated) so we
            # don't overwrite an existing value when promoting "interests" etc.
            new_value = captured if not current else f"{current}, {captured}"
            updated = profile_store.patch({field: new_value})
            return {
                "status": "promoted",
                "field": field,
                "stored": new_value,
                "profile": updated,
            }

        if choice == "save":
            try:
                memory_store.store_profile_fact(candidate["text"])
                return {"status": "saved"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        raise HTTPException(status_code=400, detail=f"Unknown decision: {choice}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"decide_fact error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/facts/policies")
async def list_fact_policies():
    """Return current per-pattern memory-approval policies."""
    fact_approvals = get_approval_store(
        "fact_patterns", root_dir=os.path.join(settings.DATA_DIR, "approvals")
    )
    return {"policies": fact_approvals.all(), "patterns": fact_pattern_keys()}


@app.delete("/memory/facts/policies/{pattern}")
async def revoke_fact_policy(pattern: str):
    """Drop the auto-save / never-save rule for a pattern (back to 'ask')."""
    fact_approvals = get_approval_store(
        "fact_patterns", root_dir=os.path.join(settings.DATA_DIR, "approvals")
    )
    removed = fact_approvals.remove(pattern)
    return {"removed": removed, "pattern": pattern}


# ---------- Phase E1: Project Intelligence ----------

def _maybe_activate_project(intent: str, message: str) -> None:
    """When the user says 'continue X' / 'switch to project X' / 'work on X',
    fuzzy-match X against existing projects and activate the best fit on the
    current session. Pure context switch — no chip, no LLM call, no write
    beyond updating ConversationMemory state."""
    if not project_store or not conversation_memory:
        return
    if (intent or "").lower() != "project_continue":
        return
    # The intent keyword list mentions phrases like "continue my", "work on my",
    # "switch to project". Pull whatever comes after the verb as the query.
    text = (message or "").strip()
    # Split on the first known cue verb; take the tail.
    cues = (
        "continue working on", "switch to project", "switch to my",
        "open project", "load project",
        "continue my", "continue the",
        "resume my", "resume the",
        "work on my", "work on the",
        "back to my", "back to the",
    )
    tail = ""
    lower = text.lower()
    for cue in cues:
        idx = lower.find(cue)
        if idx >= 0:
            tail = text[idx + len(cue):].strip()
            break
    if not tail:
        return
    # Strip trailing punctuation and qualifiers like "project", "again".
    tail = tail.rstrip(".!?,;:").strip()
    for stop in (" project", " again", " now", " please"):
        if tail.lower().endswith(stop):
            tail = tail[: -len(stop)].strip()
    proj = project_store.find_by_name_or_slug(tail)
    if not proj:
        logger.info(f"project_continue: no project matched '{tail}'")
        return
    conversation_memory.active_project_id = proj["id"]
    project_store.link_session(proj["id"], conversation_memory.session_id)
    conversation_memory._save_to_disk()
    logger.info(f"project_continue: activated '{proj['name']}' on session {conversation_memory.session_id[:8]}…")


class ProjectCreateRequest(BaseModel):
    name: str
    stack: Optional[str] = ""
    description: Optional[str] = ""
    notes: Optional[str] = ""
    status: Optional[str] = None


class ProjectPatchRequest(BaseModel):
    name: Optional[str] = None
    stack: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class ProjectTaskRequest(BaseModel):
    text: str


class ProjectTaskPatchRequest(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None


class ProjectActivateRequest(BaseModel):
    project_id: Optional[str] = None  # null = clear active project


@app.get("/projects")
async def list_projects(include_archived: bool = False):
    """List projects newest-updated first. Archived hidden by default."""
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    return {
        "projects": project_store.list(include_archived=include_archived),
        "statuses": list(PROJECT_STATUSES),
        "active_project_id": (
            conversation_memory.active_project_id if conversation_memory else None
        ),
    }


@app.post("/projects")
async def create_project(request: ProjectCreateRequest):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    try:
        proj = project_store.create(
            request.name,
            stack=request.stack or "",
            description=request.description or "",
            notes=request.notes or "",
            status=request.status,
        )
        return {"project": proj}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/projects/{project_id}")
async def get_project(project_id: str):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    proj = project_store.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": proj}


@app.patch("/projects/{project_id}")
async def patch_project(project_id: str, request: ProjectPatchRequest):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    proj = project_store.patch(project_id, **request.dict(exclude_none=True))
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": proj}


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    ok = project_store.delete(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    # If the active project was just deleted, clear the session pointer too.
    if conversation_memory and conversation_memory.active_project_id == project_id:
        conversation_memory.active_project_id = None
        conversation_memory._save_to_disk()
    return {"status": "deleted", "id": project_id}


@app.post("/projects/{project_id}/tasks")
async def add_project_task(project_id: str, request: ProjectTaskRequest):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    task = project_store.add_task(project_id, request.text)
    if not task:
        raise HTTPException(status_code=404, detail="Project not found or empty text")
    return {"task": task}


@app.patch("/projects/{project_id}/tasks/{task_id}")
async def patch_project_task(project_id: str, task_id: str, request: ProjectTaskPatchRequest):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    task = project_store.patch_task(
        project_id, task_id, text=request.text, done=request.done
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task}


@app.delete("/projects/{project_id}/tasks/{task_id}")
async def remove_project_task(project_id: str, task_id: str):
    if not project_store:
        raise HTTPException(status_code=503, detail="Project store not initialized")
    ok = project_store.remove_task(project_id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "deleted"}


@app.post("/projects/active")
async def set_active_project(request: ProjectActivateRequest):
    """Activate (or clear) the project attached to the current session."""
    if not conversation_memory:
        raise HTTPException(status_code=503, detail="Conversation memory not initialized")
    pid = request.project_id
    if pid:
        proj = project_store.get(pid) if project_store else None
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        conversation_memory.active_project_id = pid
        project_store.link_session(pid, conversation_memory.session_id)
    else:
        conversation_memory.active_project_id = None
    conversation_memory._save_to_disk()
    return {
        "active_project_id": conversation_memory.active_project_id,
        "session_id": conversation_memory.session_id,
    }


# ---------- Phase D: MongoDB sync + Sessions sidebar ----------

@app.get("/workspace")
async def workspace_snapshot(
    include_clipboard: bool = True,
    include_processes: bool = True,
    include_windows: bool = True,
):
    """Return a snapshot of the user's current desktop — active window, open
    windows, clipboard, running apps.

    READ-ONLY. Nothing here mutates the user's machine. This is the data layer
    behind the workspace_query / read_clipboard macros, and a hook for future
    "continue my project" type flows. Returns gracefully on Linux (Render)
    where pygetwindow isn't available.
    """
    try:
        from automation.workspace import get_workspace_snapshot, format_snapshot_markdown
    except Exception as e:
        return {"available": False, "reason": str(e)}
    snap = get_workspace_snapshot(
        include_clipboard=include_clipboard,
        include_processes=include_processes,
        include_windows=include_windows,
    )
    return {
        "available": snap.platform_supported,
        "markdown": format_snapshot_markdown(snap, include_clipboard=include_clipboard),
        "snapshot": snap.to_dict(),
    }


@app.get("/mongo/status")
async def mongo_status():
    """Return cloud-sync state for the topbar pill and the settings UI."""
    m = mongo_sync_singleton()
    if not m:
        return {
            "enabled": settings.MONGO_ENABLED,
            "configured": bool(settings.MONGO_URI),
            "connected": False,
        }
    return {"enabled": settings.MONGO_ENABLED, **m.status()}


@app.get("/sessions")
async def list_sessions(limit: int = 30):
    """List recent sessions for the Sessions sidebar.

    When Mongo isn't connected we still return at least the current session
    so the sidebar isn't empty in local-only mode.
    """
    if not conversation_memory:
        raise HTTPException(status_code=503, detail="Conversation memory not initialized")
    current = {
        "_id": conversation_memory.session_id,
        "title": conversation_memory.session_title or "Untitled chat",
        "started_at": conversation_memory.session_started_at,
        "verbatim_count": len(conversation_memory.current_session),
        "is_current": True,
    }
    m = mongo_sync_singleton()
    if not m or not m.available:
        return {"sessions": [current], "source": "local", "available": False}
    sessions = m.list_sessions(limit=limit)
    seen_current = False
    for s in sessions:
        s["is_current"] = (s.get("_id") == conversation_memory.session_id)
        if s["is_current"]:
            seen_current = True
    # If the current session hasn't been synced yet (e.g. fresh after clear),
    # surface it at the top so the UI doesn't show two competing "Untitled".
    if not seen_current:
        sessions.insert(0, current)
    return {"sessions": sessions, "source": "mongo", "available": True}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Return one full session document for inspection or resume."""
    m = mongo_sync_singleton()
    # Local fallback: if asking for the current session, serve from memory.
    if conversation_memory and session_id == conversation_memory.session_id:
        return {
            "_id": conversation_memory.session_id,
            "title": conversation_memory.session_title,
            "started_at": conversation_memory.session_started_at,
            "verbatim": conversation_memory.current_session,
            "rolling_summary": conversation_memory.get_running_summary(),
            "source": "local",
        }
    if not m or not m.available:
        raise HTTPException(status_code=503, detail="MongoDB sync not available")
    doc = m.get_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    doc["source"] = "mongo"
    return doc


@app.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str):
    """Make the chosen session the active one.

    Loads the verbatim turns + rolling summary into ConversationMemory and
    persists to disk so a server restart preserves the resumed state.
    """
    if not conversation_memory:
        raise HTTPException(status_code=503, detail="Conversation memory not initialized")
    # Same source-fallback as get_session: support local self-resume.
    if session_id == conversation_memory.session_id:
        return {
            "status": "already_current",
            "session_id": session_id,
            "verbatim_count": len(conversation_memory.current_session),
        }
    m = mongo_sync_singleton()
    if not m or not m.available:
        raise HTTPException(status_code=503, detail="MongoDB sync not available")
    doc = m.get_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    conversation_memory.resume_from(doc)
    return {
        "status": "resumed",
        "session_id": conversation_memory.session_id,
        "verbatim_count": len(conversation_memory.current_session),
        "verbatim": conversation_memory.current_session,
        "rolling_summary": conversation_memory.get_running_summary(),
        "title": conversation_memory.session_title,
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Hard-delete one chat session from Mongo.

    Refuses to delete the active session — the UI should rotate to a new
    session first (POST /sessions/new) before deleting the previously-active
    one. This keeps Hunt's in-memory pointer consistent with what's on disk.
    """
    if not conversation_memory:
        raise HTTPException(status_code=503, detail="Conversation memory not initialized")
    if session_id == conversation_memory.session_id:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the active session. Start a new chat first."
        )
    m = mongo_sync_singleton()
    if not m or not m.available:
        raise HTTPException(status_code=503, detail="MongoDB sync not available — past chats live only in Mongo")
    removed = m.delete_session(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


@app.post("/sessions/new")
async def new_session():
    """Start a fresh session (preserves the previous one in Mongo)."""
    if not conversation_memory:
        raise HTTPException(status_code=503, detail="Conversation memory not initialized")
    # Force-flush the current session to Mongo before rotating so the sidebar
    # surfaces it as a completed chat.
    conversation_memory._sync_to_mongo()
    conversation_memory.clear_session()
    return {
        "status": "ok",
        "session_id": conversation_memory.session_id,
        "title": conversation_memory.session_title,
    }


@app.post("/conversation/clear")
async def clear_conversation():
    """Forget the in-process running thread so the next /chat starts fresh."""
    try:
        if not conversation_memory:
            raise HTTPException(status_code=503, detail="Conversation memory not initialized")
        dropped = conversation_memory.clear_session()
        return {"status": "ok", "dropped": dropped}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Clear conversation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/models/roles")
def get_model_roles():  # sync: get_available_models() hits Ollama (blocking)
    """Return the current role→model map plus the intent→role mapping the
    router uses, so the UI can show which model handles which intent."""
    try:
        if not model_router:
            raise HTTPException(status_code=503, detail="Model router not initialized")
        roles = model_router.get_roles()
        available = llm_engine.get_available_models() if llm_engine else []
        # OpenRouter (cloud) models never appear in Ollama's tag list, so add
        # any currently-configured "openrouter:" role models to the picker list.
        cloud_models = sorted({m for m in roles.values() if m.startswith("openrouter:")})
        return {
            "enabled": settings.LLM_ROUTING_ENABLED,
            "fallback_role": settings.LLM_ROLE_FALLBACK,
            "roles": roles,
            "intent_map": INTENT_TO_ROLE,
            "known_roles": list(KNOWN_ROLES),
            "available_models": available + [m for m in cloud_models if m not in available],
            "openrouter_enabled": bool(settings.OPENROUTER_API_KEY),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get role models error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/models/roles")
def set_model_roles(request: dict):  # sync: keeps it off the event loop, consistent with the GET
    """Replace the runtime role→model map. Restart restores from settings."""
    try:
        if not model_router:
            raise HTTPException(status_code=503, detail="Model router not initialized")
        # Accept either {"roles": {...}} or just the bare map.
        raw = request.get("roles") if isinstance(request, dict) and "roles" in request else request
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="Body must be a {role: model} object")
        applied = model_router.set_roles(raw)
        return {"status": "ok", "roles": applied}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Set role models error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/model")
def switch_model(request: ModelSwitchRequest):  # sync: get_available_models() hits Ollama (blocking)
    """Switch the active LLM at runtime (persists only for the current process)."""
    try:
        if not llm_engine:
            raise HTTPException(status_code=503, detail="LLM not initialized")
        # "openrouter:" models are cloud-routed and won't be in Ollama's tags;
        # only validate local models against the pulled list.
        if not request.model.startswith("openrouter:"):
            available = llm_engine.get_available_models()
            if available and request.model not in available:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{request.model}' is not pulled in Ollama. Available: {available}",
                )
        elif not settings.OPENROUTER_API_KEY:
            raise HTTPException(
                status_code=400,
                detail="OpenRouter model requested but OPENROUTER_API_KEY is not set.",
            )
        llm_engine.set_model(request.model)
        return {"status": "ok", "model": request.model}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Model switch error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/status")
def get_status():  # sync: get_available_models() hits Ollama (blocking); UI polls this often
    """Get Jarvis status"""
    try:
        models = llm_engine.get_available_models() if llm_engine else []
        memory_count = len(memory_store.get_all_memories()) if memory_store else 0
        active_model = llm_engine.model_name if llm_engine else settings.LLM_MODEL

        return {
            "status": "running",
            "app_name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "llm_model": active_model,
            "available_models": models,
            "memory_items": memory_count,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Status error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Serve the Hunt v2 dashboard (default UI).

    v2 is now the primary UI at "/". The original UI lives at "/v1" for
    anyone who still wants the dense control-panel layout.
    """
    v2_path = os.path.join(UI_DIR, "v2", "index.html")
    if os.path.exists(v2_path):
        return FileResponse(v2_path)
    # Fallback: if v2 is missing for some reason, serve the legacy UI so
    # the deployment isn't a blank 404.
    legacy_path = os.path.join(UI_DIR, "index.html")
    if os.path.exists(legacy_path):
        return FileResponse(legacy_path)
    return {
        "message": "Welcome to Hunt - Your Personal AI Assistant",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/v1")
async def root_v1():
    """Serve the legacy (v1) UI — the original cream/terracotta dashboard."""
    legacy_path = os.path.join(UI_DIR, "index.html")
    if os.path.exists(legacy_path):
        return FileResponse(legacy_path)
    raise HTTPException(status_code=404, detail="legacy UI not found")


@app.get("/v2")
async def root_v2_alias():
    """Back-compat alias — /v2 still works for anyone with the URL bookmarked.
    Just serves the same file "/" serves."""
    return await root()


if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"API running on http://{settings.API_HOST}:{settings.API_PORT}")
    
    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        workers=settings.API_WORKERS,
        log_level=settings.LOG_LEVEL.lower()
    )
