# Hunt — Capabilities Reference

> A complete map of what Hunt (Doondy) can do, organized by capability.
> For each capability: **what it does**, **which libraries** power it, **which modules** in the codebase implement it, and **which HTTP endpoints / functions** expose it.

Last updated: 2026-06-04

---

## Table of contents

1. [Conversational chat (text + streaming)](#1-conversational-chat)
2. [Voice input — speech-to-text](#2-voice-input--speech-to-text)
3. [Voice output — text-to-speech](#3-voice-output--text-to-speech)
4. [Multi-LLM routing with fallback chains](#4-multi-llm-routing-with-fallback-chains)
5. [Response Composer — personality layer](#5-response-composer--personality-layer)
6. [Long-term memory (vector store)](#6-long-term-memory-vector-store)
7. [Profile facts (curated personal data)](#7-profile-facts-curated-personal-data)
8. [Projects + tasks](#8-projects--tasks)
9. [Sessions + conversation history](#9-sessions--conversation-history)
10. [MongoDB Atlas cloud sync](#10-mongodb-atlas-cloud-sync)
11. [Action proposals + approval chips](#11-action-proposals--approval-chips)
12. [Action execution (desktop + browser automation)](#12-action-execution-desktop--browser-automation)
13. [Fact extraction + chip-based saving](#13-fact-extraction--chip-based-saving)
14. [Voice macros (deterministic recipes)](#14-voice-macros-deterministic-recipes)
15. [Briefing composer](#15-briefing-composer)
16. [Workspace awareness](#16-workspace-awareness)
17. [Web search (live context)](#17-web-search-live-context)
18. [Two-UI deployment (legacy + v2 dashboard)](#18-two-ui-deployment-legacy--v2-dashboard)
19. [The v2 orb (Obsidian Nexus)](#19-the-v2-orb-obsidian-nexus)
20. [Settings + preferences](#20-settings--preferences)
21. [Cloud deployment (Render)](#21-cloud-deployment-render)

Each capability section includes a **"Where to look"** subsection so you can jump straight to the code.

---

## 1. Conversational chat

**What it does:** User sends a message, Hunt streams a reply token-by-token using NDJSON-over-HTTP. Supports non-streaming (`/chat`) and streaming (`/chat/stream`) modes.

| Aspect | Detail |
|---|---|
| **Libraries** | `fastapi` (server), `requests` (LLM HTTP client), `pydantic` (request validation) |
| **Module** | [app/main.py](app/main.py) — `chat()` and `chat_stream()` |
| **Endpoint** | `POST /chat` (sync), `POST /chat/stream` (streaming NDJSON) |
| **Event types streamed** | `meta`, `token`, `done`, `error`, `action_proposal`, `action_executed`, `fact_proposal`, `macro_data` |
| **Backed by** | `LLMEngine.generate_stream()` in [brain/llm_engine.py](brain/llm_engine.py) |

**Streaming protocol** (NDJSON — each line is one JSON event):
```json
{"type":"meta", "intent":"code_help", "model":"openrouter:claude-sonnet-4", "role":"coder"}
{"type":"token", "content":"def "}
{"type":"token", "content":"bubble_sort"}
{"type":"done", "response":"def bubble_sort(arr): ...", "intent":"code_help"}
```

---

## 2. Voice input — speech-to-text

**What it does:** Browser records mic audio, encodes as 16kHz PCM WAV in JavaScript, POSTs to backend, backend runs Whisper, returns transcript.

| Aspect | Detail |
|---|---|
| **Libraries** | `openai-whisper` (Whisper model), `soundfile` (WAV decode), `sounddevice` (server-side mic, optional) |
| **Front-end** | Web Audio API (`AudioContext`, `ScriptProcessorNode`) + custom WAV encoder in [ui/static/v2/app.js](ui/static/v2/app.js) — `startPcmCapture()`, `stopPcmCapture()`, `encodeWav()`, `downsampleTo16k()` |
| **Back-end module** | [voice/voice_engine.py](voice/voice_engine.py) — `SpeechToText` class |
| **Endpoint** | `POST /voice/transcribe` (multipart form upload), `GET /voice/devices` (list mics) |
| **Key methods** | `transcribe_bytes_with_diagnostics()`, `_analyze_audio_array()`, `_looks_like_low_confidence_result()` |
| **Whisper model** | Configurable via `SPEECH_TO_TEXT_MODEL` env: `tiny` / `base` / `small` / `medium` / `large` |
| **Hallucination filter** | `_HALLUCINATION_MARKERS` rejects known fallback phrases ("personal ai assistant", "thanks for watching", etc.) |
| **Threadpool dispatch** | Whisper runs in `starlette.concurrency.run_in_threadpool` so it doesn't block the async event loop |

---

## 3. Voice output — text-to-speech

**What it does:** Speaks Hunt's replies aloud. Browser TTS in v2; system TTS (pyttsx3 / edge-tts) available server-side.

| Aspect | Detail |
|---|---|
| **Browser TTS** | `SpeechSynthesis` Web API — see `speak()` in [ui/static/v2/app.js](ui/static/v2/app.js) |
| **Server TTS lib** | `pyttsx3` (offline, OS native voices), `edge-tts` (Microsoft Edge cloud voices, optional) |
| **Server module** | [voice/voice_engine.py](voice/voice_engine.py) — `TextToSpeech` class |
| **Key methods** | `speak()`, `synthesize_to_file()`, `synthesize_to_bytes()` |
| **Voice setting** | `TTS_VOICE` env (e.g. `en-US-AmberNeural`) |
| **Markdown strip** | `stripMarkdown()` in the JS layer cleans markdown before speaking so the user doesn't hear "asterisk asterisk bold asterisk asterisk" |

---

## 4. Multi-LLM routing with fallback chains

**What it does:** Each chat is routed to a model based on intent (`brain`/`coder`/`fast`/`vision`), with automatic fallback to other providers if the primary fails (rate limit, 402, 5xx, timeout).

| Aspect | Detail |
|---|---|
| **Libraries** | `requests` (HTTP), `ollama` (local Python client) |
| **Modules** | [brain/llm_engine.py](brain/llm_engine.py) (`LLMEngine`), [brain/model_router.py](brain/model_router.py) (`ModelRouter`), [brain/context_manager.py](brain/context_manager.py) (intent detection via `Reasoning.analyze_intent`) |
| **Supported providers** | Ollama (local), OpenRouter, Groq, Google AI Studio (Gemini direct) |
| **Provider prefix syntax** | `openrouter:anthropic/claude-sonnet-4`, `groq:llama-3.3-70b-versatile`, `gemini:gemini-2.0-flash-exp`, or no prefix → Ollama |
| **Roles** | `brain` (reasoning), `coder` (code gen), `fast` (greetings, quick replies, voice), `vision` (images) |
| **Fallback chain config** | `LLM_ROLE_MODELS_JSON` env. Each role value can be a **string** (single model) or **list of strings** (ordered fallback chain) |
| **Key functions** | `ModelRouter.pick_model()`, `LLMEngine.generate_stream()` (dispatches + iterates chain), `_provider_and_model()`, `_chat_openai_compat()`, `_stream_openai_compat()` |
| **Endpoints** | `GET /models/roles` (read map), `PUT /models/roles` (runtime swap, doesn't persist), `POST /model` (set default model name) |

**Chain behavior:** Tries primary, on pre-token error (timeout / 4xx / 5xx) silently moves to next candidate. If LLM has already started streaming and then errors, the chain stops (can't restart mid-stream).

---

## 5. Response Composer — personality layer

**What it does:** Wraps raw LLM output with a randomized opening line ("Here you go.") and a closing line that suggests next steps ("Want me to explain it, optimize it…?"). Makes responses feel less like ChatGPT and more like a colleague.

| Aspect | Detail |
|---|---|
| **Library** | Standard library `random` only — pure templates, zero latency |
| **Module** | [brain/response_composer.py](brain/response_composer.py) |
| **Public API** | `compose_intro(intent, role, voice_mode)`, `compose_outro(intent, response_text, role, voice_mode)`, `chunk_text(text, chunk_size)` |
| **Wired into** | [app/main.py](app/main.py) `event_stream()` inside `/chat/stream` — intros stream before the LLM, outros after the LLM's "done" event |
| **Per-intent templates** | `INTROS_TEXT` (code_help, reasoning, search, task, open_app, close_app, file_operation), `OUTROS_CODE`, `OUTROS_REASONING`, `OUTROS_SEARCH` |
| **Voice mode behavior** | Bypassed entirely — voice replies stay tight, no "Here you go." spoken at the user |
| **Heuristics** | `_looks_like_code()` (detects code blocks), `_is_trivial_reply()` (skips outro for short replies) |

---

## 6. Long-term memory (vector store)

**What it does:** Stores conversation exchanges + profile facts + project context as embeddings in ChromaDB. Retrieves the top-K most relevant memories per query.

| Aspect | Detail |
|---|---|
| **Libraries** | `chromadb` (vector DB), `sentence-transformers` (embeddings, default `all-MiniLM-L6-v2`) |
| **Module** | [memory/memory_store.py](memory/memory_store.py) — `MemoryStore`, `ConversationMemory`, `ProjectMemory` |
| **Storage path** | `./data/chroma` (configurable via `CHROMA_PATH`) |
| **Key methods** | `store_memory()`, `retrieve_memories()`, `update_memory()`, `delete_memory()`, `store_profile_fact()`, `get_profile_facts()`, `add_exchange()`, `get_context()` |
| **Endpoints** | `POST /memory/store`, `GET /memory/retrieve`, `GET /memory/profile`, `DELETE /memory/profile`, `DELETE /memory/profile/{fact_id}`, `GET /memory/session` |
| **Conversation memory** | `ConversationMemory.add_exchange()` writes every chat turn; `get_context()` retrieves relevant past turns for the current prompt |
| **Auto-summarization** | After every 10 exchanges, generates a running summary using the LLM (see `get_running_summary()`) |

---

## 7. Profile facts (curated personal data)

**What it does:** Stores 8 first-class profile fields (name, occupation, projects, interests, tone, schedule, contacts, goals) plus a free-form fact stream. Injected into every chat prompt.

| Aspect | Detail |
|---|---|
| **Library** | Standard library (JSON file persistence) |
| **Module** | [memory/profile.py](memory/profile.py) — `ProfileStore` |
| **Storage** | `./data/profile.json` |
| **Key methods** | `get()`, `replace()`, `patch()`, `clear()`, `is_empty()`, `format_for_prompt()` |
| **Endpoints** | `GET /profile`, `POST /profile`, `DELETE /profile` |
| **Prompt injection** | `format_for_prompt()` is called inside `_build_chat_context_and_history()` in main.py — the profile becomes part of the system context every chat |

---

## 8. Projects + tasks

**What it does:** First-class "project" objects with stack, status, description, notes, and a task list. One project can be active per session and gets injected into the system prompt.

| Aspect | Detail |
|---|---|
| **Library** | Standard library (JSON file persistence) |
| **Module** | [memory/project_store.py](memory/project_store.py) — `ProjectStore` |
| **Storage** | `./data/projects.json` |
| **Statuses** | `active`, `paused`, `done`, `archived` |
| **Key methods** | `list()`, `get()`, `find_by_name_or_slug()`, `create()`, `patch()`, `delete()`, `add_task()`, `patch_task()`, `remove_task()`, `link_session()`, `format_active_block()` |
| **Endpoints** | `GET /projects`, `POST /projects`, `GET /projects/{id}`, `DELETE /projects/{id}`, `POST /projects/{id}/tasks`, `DELETE /projects/{id}/tasks/{task_id}`, `POST /projects/active` |
| **Intent-driven activation** | Saying "continue my X" triggers the `project_continue` intent — `_maybe_activate_project()` in main.py looks up the project and activates it for the session |

---

## 9. Sessions + conversation history

**What it does:** Each chat session is persisted to disk and (optionally) MongoDB. Past sessions are listable + resumable. Auto-titles generated from the first exchange.

| Aspect | Detail |
|---|---|
| **Library** | Standard library (JSON file persistence) + MongoDB (sync layer) |
| **Module** | [memory/memory_store.py](memory/memory_store.py) `ConversationMemory` class |
| **Storage** | `./data/sessions/current.json` (active), past sessions also on disk |
| **Key methods** | `add_exchange()`, `resume_from()`, `get_context()`, `get_session_messages()`, `clear_session()`, `get_running_summary()` |
| **Endpoints** | `GET /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/resume`, `POST /sessions/new`, `POST /conversation/clear`, `GET /conversation/export` (Markdown export) |

---

## 10. MongoDB Atlas cloud sync

**What it does:** Mirrors sessions, profile, facts, action history, and projects to MongoDB Atlas for cross-device persistence. Fire-and-forget background worker.

| Aspect | Detail |
|---|---|
| **Library** | `pymongo` |
| **Module** | [memory/mongo_sync.py](memory/mongo_sync.py) — `MongoSync` class |
| **Config** | `MONGO_ENABLED`, `MONGO_URI`, `MONGO_DB`, `MONGO_DEVICE_ID` env vars |
| **Architecture** | Background worker thread + queue. Writes never block the chat path. |
| **Collections** | One per "kind": sessions, profile, facts, actions, projects |
| **Endpoint** | `GET /mongo/status` |
| **Functions** | `get_mongo_sync()`, `mongo_sync_singleton()` |

---

## 11. Action proposals + approval chips

**What it does:** Certain intents (open_app, close_app, search, open_browser) emit an `action_proposal` event after the LLM done event. The UI renders Allow / Deny / Always-allow chips. User's choice is sent back via `/actions/{id}/decide`.

| Aspect | Detail |
|---|---|
| **Library** | Standard library (regex extraction + JSON file storage) |
| **Modules** | [brain/action_proposer.py](brain/action_proposer.py) (regex-based intent → action params), [memory/approvals.py](memory/approvals.py) (`ApprovalStore` — per-action policy: ask/always/never) |
| **Action intents** | `ACTION_INTENTS = {"open_app", "close_app", "search", "open_browser"}` |
| **Functions** | `propose_action(intent, message)`, `_build()`, `_extract_after_verb()`, `_maybe_action_events()` in main.py (event generator) |
| **Endpoints** | `POST /actions/{proposal_id}/decide`, `GET /actions` (history), `DELETE /actions`, `GET /actions/policies`, `DELETE /actions/policies/{action_type}` |
| **Three policy branches** | `always` → auto-execute, yields `action_executed` event; `ask` → emit `action_proposal` with chips; `never` → silent skip |
| **Persistence** | `./data/approvals/actions.json` (per-action policy), `./data/actions_history.json` (audit trail) |

---

## 12. Action execution (desktop + browser automation)

**What it does:** When an action is approved, runs it on the host machine via PyAutoGUI (desktop) or Playwright (browser).

| Aspect | Detail |
|---|---|
| **Libraries** | `pyautogui` (mouse/keyboard/windows), `playwright` (browser), `pyperclip` (clipboard), `pygetwindow` (window control) |
| **Modules** | [automation/action_runner.py](automation/action_runner.py) (dispatcher), [automation/automation_engine.py](automation/automation_engine.py) (`DesktopAutomation`, `BrowserAutomation`) |
| **Registered actions** | `open_app`, `close_app`, `search`, `open_browser` (see `ACTION_REGISTRY`) |
| **Desktop methods** | `open_application()`, `close_application()`, `type_text()`, `press_key()`, `click()`, `get_mouse_position()`, `move_mouse()` |
| **Browser methods** | `open_browser()`, `close_browser()`, `navigate()`, `search()`, `click_element()`, `fill_form()` |
| **Dispatcher** | `run_action(action, params)` resolves the spec, lazy-imports the backend, calls the method, returns `{"status": ..., "detail": ...}` |
| **History** | `ActionHistory` class persists every attempt with the chosen policy (`always` / `ask` / `deny`) |
| **Cloud caveat** | None of these run on Render (Linux container with no GUI). Action proposals still fire, they just fail gracefully. |

---

## 13. Fact extraction + chip-based saving

**What it does:** When you say "my name is Rayan" or "I work as a software engineer", a regex layer extracts candidate facts and emits `fact_proposal` events. The UI shows Save / Skip / Always-save / "→ Name" chips.

| Aspect | Detail |
|---|---|
| **Library** | Standard library `re` |
| **Module** | [memory/facts_extractor.py](memory/facts_extractor.py) — `extract_facts(message)`, `pattern_keys()` |
| **Patterns** | name, occupation, projects, interests, tone, schedule, contacts, goals — same 8 as the profile |
| **Wired into** | `_maybe_fact_events()` in main.py |
| **Endpoints** | `POST /memory/facts/{fact_id}/decide`, `GET /memory/facts/policies`, `DELETE /memory/facts/policies/{pattern}` |
| **Decisions** | `save`, `skip`, `always_save` (sets per-pattern auto policy), `never_again`, `promote` (writes the captured value into the structured profile field) |

---

## 14. Voice macros (deterministic recipes)

**What it does:** Some intents short-circuit the LLM entirely — they run a Python recipe that gathers data from the local stores and returns a pre-composed reply. Zero LLM cost, sub-100ms latency.

| Aspect | Detail |
|---|---|
| **Library** | Standard library |
| **Module** | [brain/macro_runner.py](brain/macro_runner.py) |
| **Registered macros** | `morning_brief`, `read_open_tasks`, `wrap_up_session`, `workspace_query`, `read_clipboard` |
| **Trigger phrases** | Defined in `intents` dict inside [brain/context_manager.py](brain/context_manager.py) — e.g. "brief me", "read tasks", "what am I working on", "read my clipboard" |
| **Public API** | `is_macro(intent)`, `run_macro(intent, ctx)` |
| **Output** | `MacroResult` dataclass: `name`, `script` (text), `structured` (rich payload), `side_effects` (list of mutations) |
| **Wired into** | `chat_stream()` in main.py — early `if is_macro(intent):` short-circuit; emits `macro_data` stream event |

---

## 15. Briefing composer

**What it does:** Aggregates profile + projects + recent actions + session state + Mongo state into one structured "briefing" — feeds the `morning_brief` macro.

| Aspect | Detail |
|---|---|
| **Library** | Standard library |
| **Module** | [app/briefing.py](app/briefing.py) — `BriefingComposer`, `BriefingPayload` |
| **Pure read** | Doesn't mutate anything. Aggregator only. |
| **Endpoint** | `GET /briefing` |
| **Returns** | Structured object + a speakable script under ~45 seconds at normal pace |
| **Sources** | profile_store, project_store, action_history, conversation_memory, mongo_sync |

---

## 16. Workspace awareness

**What it does:** Hunt can see what desktop window is active, what other windows are open, what's in the clipboard, and what interesting apps (VS Code, Chrome, Slack…) are running. Powers "what am I working on" and "read my clipboard".

| Aspect | Detail |
|---|---|
| **Libraries** | `pygetwindow` (windows), `pyperclip` (clipboard), `psutil` (process list) |
| **Module** | [automation/workspace.py](automation/workspace.py) |
| **Public API** | `get_workspace_snapshot(include_clipboard, include_processes, include_windows, max_clipboard_chars)`, `format_snapshot_markdown(snap)`, `format_snapshot_speakable(snap)` |
| **Dataclasses** | `WindowInfo`, `WorkspaceSnapshot` |
| **Internal probes** | `_get_active_and_windows()`, `_get_clipboard()`, `_get_processes()` |
| **Interesting processes filter** | `INTERESTING_PROCESSES` dict maps process names to friendly labels — VS Code, Cursor, Chrome, Edge, Firefox, Notepad, PyCharm, IntelliJ, Slack, Discord, Spotify, Outlook, Figma, Ollama, … |
| **Macros** | `workspace_query`, `read_clipboard` in [brain/macro_runner.py](brain/macro_runner.py) |
| **Endpoint** | `GET /workspace` — returns `{available, markdown, snapshot}` |
| **Privacy** | Read-only. Snapshot returned only to the caller (your browser). Not auto-sent to cloud LLMs. Degrades gracefully on Linux container (Render): returns `{available: false}`. |

---

## 17. Web search (live context)

**What it does:** For queries about current events or external facts, Hunt searches the web and injects the top 3 results into the LLM prompt.

| Aspect | Detail |
|---|---|
| **Library** | `duckduckgo-search` |
| **Module** | [app/main.py](app/main.py) — `should_use_live_search()`, `search_web()`, `clean_html()`, `build_live_context()` |
| **Config** | `WEB_SEARCH_ENABLED`, `WEB_SEARCH_RESULTS` env |
| **Toggleable per chat** | `ChatRequest.use_live_search` field |
| **Heuristic detection** | Looks for keywords like "today", "current", "latest", company names, news terms |

---

## 18. Two-UI deployment (legacy + v2 dashboard)

**What it does:** Hunt serves two UIs side-by-side. The legacy UI at `/` is the original cream/terracotta look with topbar pills. The new v2 dashboard at `/v2` is the dark orb experience.

| Aspect | Detail |
|---|---|
| **Static files** | [ui/static/](ui/static/) (legacy: `app.js`, `index.html`, `styles.css`), [ui/static/v2/](ui/static/v2/) (`app.js`, `orb.js`, `styles.css`, `index.html`) |
| **Endpoints** | `GET /` (legacy), `GET /v2` (dashboard) |
| **FastAPI mount** | `app.mount("/static", StaticFiles(directory=UI_DIR), name="static")` |
| **Shared backend** | Both UIs hit the same API endpoints — they're just different presentation layers |

---

## 19. The v2 orb (Obsidian Nexus)

**What it does:** Real-time canvas visualization of Hunt's state — 80 floating hexagonal fragments + 6 satellites + central hex core. Reacts to chat state and live mic audio.

| Aspect | Detail |
|---|---|
| **Tech** | Pure HTML5 Canvas 2D + pseudo-3D math (no WebGL, no Three.js) |
| **Module** | [ui/static/v2/orb.js](ui/static/v2/orb.js) — `ObsidianNexus` class |
| **API** | `setMode("idle"\|"listening"\|"thinking"\|"speaking")`, `setTheme("dark"\|"light")`, `attachStream(MediaStream)`, `detachStream()`, `getBands()`, `pause()`, `resume()`, `size()` |
| **Audio reactivity** | `AnalyserNode` from Web Audio API. Bands (low / mid / high / amp) drive radial pulse and fragment heat during listening. |
| **Pointer drag** | Mouse / touch rotates the orb (`dragX`, `dragY`) |
| **Performance** | One RAF loop, z-sorted draw, paused when Assistant view is off-screen |

---

## 20. Settings + preferences

**What it does:** v2 settings drawer with theme, reply length, voice toggles, memory toggle, model routing.

| Aspect | Detail |
|---|---|
| **Front-end persistence** | `localStorage` key `hunt.v2.prefs` |
| **Module** | [ui/static/v2/app.js](ui/static/v2/app.js) — `loadPrefs()`, `savePrefs()`, `applyTheme()` |
| **Settings** | `theme` (dark/light), `replyLength` (short/normal/detailed), `speakReplies`, `speechRate`, `useMemory`, `liveSearch`, `neuralBoost` |
| **Server-side counterparts** | `ChatRequest.response_length`, `ChatRequest.include_context`, `ChatRequest.use_live_search` — JS sends prefs on every chat call |
| **Drawer UI** | Slide-in from the right, Esc to close, scrim click to close |

---

## 21. Cloud deployment (Render)

**What it does:** Hunt can run as a Docker container on Render with `OPENROUTER_API_KEY` and `MONGODB_URI` env vars. Same code, same UI.

| Aspect | Detail |
|---|---|
| **Files** | [Dockerfile.render](Dockerfile.render), [render.yaml](render.yaml), [requirements_render.txt](requirements_render.txt), [.dockerignore](.dockerignore), [RENDER_DEPLOY.md](RENDER_DEPLOY.md) |
| **Render base** | `python:3.11-slim` + `ffmpeg` + `libsndfile1` |
| **Entry command** | `python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001}` |
| **Caveats on cloud** | Desktop automation (PyAutoGUI, pygetwindow) is unavailable — no GUI in the container. The workspace endpoint returns `{available: false}`. All cloud roles route through OpenRouter / Groq / Gemini. |
| **Persistent disk** | Only on paid plans. On free plan, Chroma data resets on every container restart; Profile facts in MongoDB Atlas survive. |

---

## Dependency manifest at a glance

### Backend (Python)

| Category | Packages |
|---|---|
| **Web framework** | `fastapi`, `uvicorn[standard]`, `python-multipart` |
| **Data validation** | `pydantic`, `pydantic-settings`, `python-dotenv` |
| **HTTP** | `requests`, `httpx` |
| **LLM clients** | `ollama` (local), `openai` (used for OpenAI-compat providers like OpenRouter / Groq / Gemini) |
| **Voice** | `openai-whisper`, `soundfile`, `sounddevice`, `pyttsx3` |
| **Memory** | `chromadb`, `sentence-transformers`, `numpy`, `pymongo` |
| **Automation** | `pyautogui`, `playwright`, `pyperclip`, `pygetwindow`, `psutil` |
| **Vision (optional)** | `opencv-python`, `pytesseract`, `easyocr` |
| **Web search** | `duckduckgo-search` |

### Frontend (Browser-only — no build step)

| Category | API / Library |
|---|---|
| **Streaming chat** | `fetch` + `ReadableStream` (NDJSON parser hand-written) |
| **Voice input** | `navigator.mediaDevices.getUserMedia()` + `AudioContext` + `ScriptProcessorNode` + custom WAV encoder |
| **Voice output** | `SpeechSynthesis` Web API |
| **Orb visualization** | HTML5 Canvas 2D + custom 3D math |
| **Persistence** | `localStorage` |

---

## Capability gap (NOT yet built)

These are on the roadmap but not implemented yet:

| Capability | What it would take |
|---|---|
| **Google Calendar / Docs / Sheets / Drive** | `google-api-python-client` + OAuth flow + a few hundred lines per integration |
| **GitHub integration** | `PyGithub` or REST calls, OAuth |
| **VS Code workspace awareness** | Parse VS Code's `globalStorage`, read open editors via Code Extension API or LSP |
| **Active browser tab content** | Browser extension OR Playwright session attached to user's existing browser |
| **Vision-based screen understanding (Claude Computer Use style)** | EasyOCR + screen capture (already in requirements but not wired) + vision model loop |
| **Screen control actions** (`paste_to_active_window`, `focus_window`, `type_text` triggered from chat) | Wire PyAutoGUI methods through `ACTION_REGISTRY` in [automation/action_runner.py](automation/action_runner.py) — ~50 lines |
| **Conversation injection of workspace context** | Settings drawer toggle that adds workspace markdown to every chat's system prompt |

---

## How the layers stack on one chat request

This is the end-to-end flow when you type "hi" in `/v2`:

```
Browser (v2/app.js)
  ↓ POST /chat/stream { message: "hi", include_context: true, voice_mode: false }
FastAPI receives → chat_stream()
  ↓ analyze_intent("hi") → "small_talk"
  ↓ is_macro("small_talk")? → no
  ↓ _build_chat_context_and_history() → injects profile, project, memory
  ↓ ModelRouter.pick_model("small_talk") → ["openrouter:gemini-2.5-flash", "gemini:flash-exp", "groq:llama-3.1-8b"]
  ↓ event_stream() generator yields:
      {"type":"meta", ...}                                    ← first
      compose_intro(small_talk) → "" (no intro for casual)    ← composer
      LLMEngine.generate_stream(model_override=chain)
        → tries openrouter:gemini-2.5-flash
        → 402 (no credit) — silent fallback
        → tries gemini:gemini-2.0-flash-exp  
        → 401 (bad key) — silent fallback
        → tries groq:llama-3.1-8b-instant
        → streams tokens: "Hi", "!", " How", " can", ...
      compose_outro(small_talk, "Hi! How can I help?") → "" (short reply)
      conversation_memory.add_exchange("hi", "Hi! How can I help?")
      {"type":"done", "response":"Hi! How can I help?", "intent":"small_talk"}
      _maybe_action_events() → no action (small_talk not in ACTION_INTENTS)
      _maybe_fact_events() → no fact extracted
  ↓ Browser updates the chat bubble incrementally
  ↓ MongoSync background worker writes exchange to Atlas
```

That's seven layers from the browser to the response, each one independent and individually testable.

---

## Where to find each file mentioned

| Path | What's in it |
|---|---|
| [app/main.py](app/main.py) | FastAPI server, all endpoints, chat orchestration |
| [app/briefing.py](app/briefing.py) | Multi-source briefing composer |
| [brain/llm_engine.py](brain/llm_engine.py) | Provider dispatch, fallback chain, OpenAI-compat client |
| [brain/model_router.py](brain/model_router.py) | Intent → role → model resolution |
| [brain/context_manager.py](brain/context_manager.py) | Intent detection (regex/keyword), context tracking |
| [brain/action_proposer.py](brain/action_proposer.py) | Extracts action params from natural language |
| [brain/macro_runner.py](brain/macro_runner.py) | Deterministic recipes for special intents |
| [brain/response_composer.py](brain/response_composer.py) | Personality wrapper templates |
| [voice/voice_engine.py](voice/voice_engine.py) | Whisper STT, pyttsx3/edge TTS, wake word |
| [memory/memory_store.py](memory/memory_store.py) | Chroma vector store, conversation memory |
| [memory/profile.py](memory/profile.py) | Structured profile facts |
| [memory/project_store.py](memory/project_store.py) | Projects + tasks |
| [memory/approvals.py](memory/approvals.py) | Per-action approval policies |
| [memory/facts_extractor.py](memory/facts_extractor.py) | Regex fact extraction |
| [memory/mongo_sync.py](memory/mongo_sync.py) | MongoDB Atlas mirror |
| [automation/action_runner.py](automation/action_runner.py) | Action dispatch + history |
| [automation/automation_engine.py](automation/automation_engine.py) | PyAutoGUI / Playwright backends |
| [automation/workspace.py](automation/workspace.py) | Workspace awareness |
| [config/settings.py](config/settings.py) | All env vars + pydantic settings |
| [ui/static/v2/app.js](ui/static/v2/app.js) | v2 frontend: chat, mic, settings, chips |
| [ui/static/v2/orb.js](ui/static/v2/orb.js) | Obsidian Nexus orb renderer |
| [ui/static/v2/styles.css](ui/static/v2/styles.css) | v2 dashboard theme |
| [ui/static/v2/index.html](ui/static/v2/index.html) | v2 layout |
