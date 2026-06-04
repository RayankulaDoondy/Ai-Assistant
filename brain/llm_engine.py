"""
LLM Engine - Core brain of Jarvis
Handles interactions with local LLM via Ollama
"""
import json
import logging
import requests
from typing import Optional, List, Dict, Iterator, Tuple
from config import settings

logger = logging.getLogger(__name__)


class LLMEngine:
    """Main LLM engine using Ollama for local inference"""
    
    def __init__(self):
        self.model_name = settings.LLM_MODEL
        self.base_url = settings.OLLAMA_BASE_URL
        self.temperature = settings.LLM_TEMPERATURE
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.num_predict = settings.LLM_NUM_PREDICT
        self.request_timeout = settings.LLM_REQUEST_TIMEOUT
        
        logger.info(f"Initializing LLM Engine with model: {self.model_name}")
        logger.info("LLM Engine initialized successfully")
    
    SYSTEM_PROMPT = (
        "You are Hunt — a calm, warm, mature personal AI assistant. Sound like a smart, "
        "well-spoken human helper, not a chatbot. Be polite, friendly, and confident. "
        "Greet naturally when greeted ('Hi! How can I help today?' / 'Good to see you.'). "
        "Match the user's tone: casual when they are casual, focused when they are focused. "
        "Avoid robotic phrases ('As an AI...', 'I am a language model'), avoid corporate "
        "filler ('I'm here to assist you with any questions!'), avoid childish or "
        "overly cheerful exclamations. Never overtalk — short replies feel natural.\n\n"

        "If the user asks you to greet someone else (e.g. 'say hi to him' / 'come here, "
        "say hello to my friend'), produce the actual greeting as if speaking to that "
        "person, in a friendly tone — do NOT narrate or explain. Example: user 'say hi "
        "to him' → 'Hi there! Nice to meet you — I'm Hunt, this person's assistant.'\n\n"

        "Give CONCRETE, SPECIFIC information: real names of places, people, products, "
        "dates, and numbers — never bracketed placeholders like [name], [location], "
        "[date]. If you genuinely do not know a fact, say so plainly instead of inventing "
        "a template.\n\n"

        "USE THE CONVERSATION HISTORY. The messages above the latest user turn are the "
        "real running thread of THIS chat. When the user says 'this chat', 'so far', "
        "'till now', 'previous answer', 'that', 'it', or asks to summarize/continue, "
        "they mean those prior turns — refer to them directly. Do not say you have no "
        "context if there is visible history above. If you have been told the user's "
        "name or preferences (via 'Known facts about the user'), use them — address "
        "them by name when natural.\n\n"

        "INPUT MAY COME FROM SPEECH-TO-TEXT and may contain misheard words, missing "
        "punctuation, or homophones (e.g. 'chart' for 'chat', 'no' for 'know', "
        "'Samarit' for 'summarize', 'their' for 'there'). When a word looks wrong given "
        "the surrounding turns, silently correct to the most likely intent and answer "
        "that — do NOT lecture the user about the typo or refuse for lack of clarity. "
        "Only ask for clarification when even the best guess would be a coin flip.\n\n"

        "Match length to the question: one or two sentences for simple questions and "
        "greetings, a short list (3-6 items) for recommendations or comparisons, a "
        "longer reply only when the user asks for detail. Do not include chain-of-thought, "
        "<think> tags, emojis, or roleplay filler. Never invent URLs, links, or video "
        "IDs; if asked for links, say plainly that you cannot browse and suggest a real "
        "search the user can run."
    )

    VOICE_SUFFIX = (
        " The user is speaking by voice, so keep the reply under "
        "{n} words and avoid lists and markdown."
    )

    CODER_SYSTEM_PROMPT = (
        "You are Hunt — a senior coding assistant. Your job IS to write code. "
        "ALWAYS produce complete, working code when asked. "
        "NEVER refuse with phrases like 'I cannot write code', 'not within my "
        "capabilities', 'I'm just an AI', or 'this is beyond what I can do'. "
        "If the user asks for code, the answer is the code.\n\n"

        "STRICT response format (follow this order, no exceptions):\n"
        "1. ONE short sentence stating what you're providing (e.g. "
        "'Here's bubble sort in Python:').\n"
        "2. EXACTLY ONE fenced code block tagged with the language "
        "(```python ... ```, ```javascript ... ```, ```sql ... ```, etc.).\n"
        "3. (Optional) Up to 3 short bullet points: usage notes, complexity, "
        "or trade-offs. ONE LINE each.\n\n"

        "FORBIDDEN in coder replies: disclaimers, apologies, 'this is a simple "
        "implementation', 'for real-world use you should...', 'please note "
        "that...', multi-paragraph explanations, restating the problem, "
        "chain-of-thought, <think> tags, emojis.\n\n"

        "Code requirements: complete and runnable; idiomatic for the language; "
        "use clear names; include type hints in Python by default; add a brief "
        "docstring or top comment; correct edge cases; no placeholder TODOs.\n\n"

        "If the user follows up with 'explain', 'optimize', 'add comments', "
        "or 'add tests', act on the code from the prior assistant turn."
    )

    # Approximate token budgets per reply-length preset. Voice replies ignore
    # these and use the smaller VOICE_RESPONSE_MAX_WORDS budget instead.
    LENGTH_BUDGETS = {"short": 160, "normal": 512, "detailed": 1024}
    # Coder replies are tight on purpose: ~500 tokens of code + ~200 of bullets.
    # Cuts off small models like qwen2.5:0.5b before they start repeating
    # "### Trade-offs" sections or echoing the system-prompt template.
    CODER_BUDGET = 700

    def _resolve_length_budget(
        self,
        response_length: Optional[str],
        voice_mode: bool,
        role: Optional[str] = None,
    ) -> int:
        if voice_mode:
            return self.num_predict
        if role == "coder":
            return self.CODER_BUDGET
        if response_length and response_length in self.LENGTH_BUDGETS:
            return self.LENGTH_BUDGETS[response_length]
        return max(self.num_predict, 512)

    @staticmethod
    def _resolve_temperature(temperature: Optional[float], fallback: float) -> float:
        if temperature is None:
            return fallback
        try:
            t = float(temperature)
        except (TypeError, ValueError):
            return fallback
        # Clamp to a sane range. >1.5 makes most chat models incoherent.
        return max(0.0, min(1.5, t))

    def _system_prompt_for(self, role: Optional[str], voice_mode: bool) -> str:
        """Pick the right persona prompt for this turn."""
        if role == "coder":
            return self.CODER_SYSTEM_PROMPT
        prompt = self.SYSTEM_PROMPT
        if voice_mode:
            prompt += self.VOICE_SUFFIX.format(n=settings.VOICE_RESPONSE_MAX_WORDS)
        return prompt

    def generate(
        self,
        prompt: str,
        context: Optional[str] = None,
        voice_mode: bool = False,
        history: Optional[List[Dict[str, str]]] = None,
        response_length: Optional[str] = None,
        temperature: Optional[float] = None,
        model_override: Optional[str] = None,
        role: Optional[str] = None,
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            prompt: The latest user message.
            context: Optional background snippets (e.g. long-term memory, web search).
            voice_mode: If True, apply the short voice-friendly word budget.
            history: Prior turns of THIS session as [{role, content}, ...]. Sent as
                proper chat messages so the model can reference them.
        """
        try:
            system_message = self._system_prompt_for(role, voice_mode)

            messages: List[Dict[str, str]] = [{"role": "system", "content": system_message}]
            if context:
                messages.append({
                    "role": "system",
                    "content": (
                        "Long-term memory / background snippets (use only if directly "
                        "relevant, otherwise ignore):\n" + context
                    ),
                })

            if history:
                # Keep only proper role-tagged turns so the chat API stays valid.
                for turn in history:
                    turn_role = turn.get("role")
                    content = (turn.get("content") or "").strip()
                    if turn_role in ("user", "assistant") and content:
                        messages.append({"role": turn_role, "content": content})

            messages.append({"role": "user", "content": prompt})

            logger.debug(
                f"Generating response: prompt={prompt[:80]!r}, history_turns={len(history or [])}, "
                f"length={response_length}, temp={temperature}, model={model_override or self.model_name}, "
                f"role={role}"
            )
            response = self._chat_ollama(
                messages,
                voice_mode=voice_mode,
                num_predict_override=self._resolve_length_budget(response_length, voice_mode, role),
                temperature_override=self._resolve_temperature(temperature, self.temperature),
                model_override=model_override,
            )
            response = self._strip_thinking(response)
            logger.info("Response generated successfully")
            return response
        except requests.Timeout:
            logger.error("Ollama generation timed out")
            return "Ollama is taking too long. Try a shorter prompt or use a smaller model."
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return "I encountered an error processing your request. Please try again."

    def generate_stream(
        self,
        prompt: str,
        context: Optional[str] = None,
        voice_mode: bool = False,
        history: Optional[List[Dict[str, str]]] = None,
        response_length: Optional[str] = None,
        temperature: Optional[float] = None,
        model_override: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Iterator[Tuple[str, str]]:
        """Stream the response token by token.

        Yields (event_type, payload) tuples:
          - ("token", chunk_text)  — partial content as it arrives
          - ("done", full_text)    — sent once at the end, complete response

        On error, yields ("error", message) and stops. The full_text in "done"
        is the same text the non-streaming `generate()` would have returned, with
        <think> tags stripped, so the caller can persist it directly.
        """
        try:
            system_message = self._system_prompt_for(role, voice_mode)

            messages: List[Dict[str, str]] = [{"role": "system", "content": system_message}]
            if context:
                messages.append({
                    "role": "system",
                    "content": (
                        "Long-term memory / background snippets (use only if directly "
                        "relevant, otherwise ignore):\n" + context
                    ),
                })
            if history:
                for turn in history:
                    turn_role = turn.get("role")
                    content = (turn.get("content") or "").strip()
                    if turn_role in ("user", "assistant") and content:
                        messages.append({"role": turn_role, "content": content})
            messages.append({"role": "user", "content": prompt})

            num_predict = self._resolve_length_budget(response_length, voice_mode, role)
            temp = self._resolve_temperature(temperature, self.temperature)

            # Normalize model_override → ordered list of candidates (fallback chain).
            # Accept a list (new) or a single string (back-compat).
            if isinstance(model_override, (list, tuple)):
                candidates = [str(m) for m in model_override if m]
            elif model_override:
                candidates = [model_override]
            else:
                candidates = [self.model_name]
            if not candidates:
                candidates = [self.model_name]

            last_error: Optional[str] = None
            for idx, model in enumerate(candidates):
                provider, real_model = self._provider_and_model(model)
                try:
                    yielded_token = False
                    if provider in self.OPENAI_COMPAT_PROVIDERS:
                        stream_iter = self._stream_openai_compat(provider, real_model, messages, num_predict, temp)
                    else:
                        stream_iter = self._stream_ollama_inner(model, messages, num_predict, temp)
                    for event_type, payload in stream_iter:
                        if event_type == "token":
                            yielded_token = True
                            yield event_type, payload
                        elif event_type == "done":
                            yield event_type, payload
                            if idx > 0:
                                logger.info(f"Chat succeeded via fallback model #{idx}: {model}")
                            return
                        elif event_type == "error":
                            last_error = payload
                            if yielded_token:
                                # Already streaming — can't restart mid-flight.
                                yield "error", payload
                                return
                            # Otherwise try the next candidate.
                            logger.warning(f"Model '{model}' failed before any token: {payload}; trying next fallback")
                            break
                        else:
                            yield event_type, payload
                    else:
                        # Stream ended without "done" or "error" — treat as success-ish.
                        return
                except requests.Timeout:
                    last_error = f"Timeout calling '{model}'"
                    logger.warning(f"{last_error}; trying next fallback")
                    continue
                except requests.HTTPError as e:
                    # 401, 402, 429, 5xx — try next candidate.
                    last_error = f"{model}: HTTP {e.response.status_code if e.response is not None else '?'} {str(e)[:120]}"
                    logger.warning(f"{last_error}; trying next fallback")
                    continue
                except Exception as e:
                    last_error = f"{model}: {e}"
                    logger.warning(f"{last_error}; trying next fallback")
                    continue

            # All candidates exhausted.
            logger.error(f"All {len(candidates)} models failed. Last error: {last_error}")
            yield "error", f"All configured models failed. Last error: {last_error or 'unknown'}"
        except Exception as e:
            logger.error(f"Error streaming response: {e}")
            yield "error", "I encountered an error processing your request. Please try again."

    def _stream_ollama_inner(
        self,
        model: str,
        messages: List[Dict],
        num_predict: int,
        temperature: float,
    ) -> Iterator[Tuple[str, str]]:
        """Streaming chat via local Ollama. Extracted from generate_stream so the
        fallback-chain dispatcher can call it uniformly alongside the cloud
        _stream_openai_compat path."""
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": "10m",
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.max_tokens,
                    "num_predict": num_predict,
                },
            },
            timeout=self.request_timeout,
            stream=True,
        )
        response.raise_for_status()

        buffer: List[str] = []
        for raw in response.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            piece = (chunk.get("message") or {}).get("content", "")
            if piece:
                buffer.append(piece)
                yield "token", piece
            if chunk.get("done"):
                break

        full = self._strip_thinking("".join(buffer)).strip()
        yield "done", full

    def _chat_ollama(
        self,
        messages: List[Dict],
        voice_mode: bool = False,
        num_predict_override: Optional[int] = None,
        temperature_override: Optional[float] = None,
        model_override: Optional[str] = None,
    ) -> str:
        """Generate a non-streaming reply, supporting the same fallback chain as
        generate_stream (model_override can be a list)."""
        # Voice replies stay tight; text replies get room for real recommendations/lists.
        if num_predict_override is not None:
            num_predict = num_predict_override
        else:
            num_predict = self.num_predict if voice_mode else max(self.num_predict, 512)
        temp = self.temperature if temperature_override is None else temperature_override

        if isinstance(model_override, (list, tuple)):
            candidates = [str(m) for m in model_override if m]
        elif model_override:
            candidates = [model_override]
        else:
            candidates = [self.model_name]
        if not candidates:
            candidates = [self.model_name]

        last_error: Optional[str] = None
        for idx, model in enumerate(candidates):
            provider, real_model = self._provider_and_model(model)
            try:
                if provider in self.OPENAI_COMPAT_PROVIDERS:
                    result = self._chat_openai_compat(provider, real_model, messages, num_predict, temp)
                else:
                    response = requests.post(
                        f"{self.base_url}/api/chat",
                        json={
                            "model": model,
                            "messages": messages,
                            "stream": False,
                            "keep_alive": "10m",
                            "options": {
                                "temperature": temp,
                                "num_ctx": self.max_tokens,
                                "num_predict": num_predict,
                            },
                        },
                        timeout=self.request_timeout,
                    )
                    response.raise_for_status()
                    data = response.json()
                    result = (data.get("message") or {}).get("content", "").strip()
                if idx > 0:
                    logger.info(f"Chat succeeded via fallback model #{idx}: {model}")
                return result
            except Exception as e:
                last_error = f"{model}: {e}"
                logger.warning(f"{last_error}; trying next fallback")
                continue

        raise RuntimeError(f"All {len(candidates)} models failed. Last error: {last_error}")

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove <think>...</think> blocks some reasoning models emit."""
        import re as _re
        cleaned = _re.sub(r"<think>[\s\S]*?</think>", "", text, flags=_re.IGNORECASE)
        return cleaned.strip()

    # ------------------------------------------------------------------ #
    # Provider routing
    # ------------------------------------------------------------------ #
    # Supported prefixes — each maps to (base_url_attr, api_key_attr, timeout_attr).
    # Anything without a known prefix is treated as a local Ollama model.
    OPENAI_COMPAT_PROVIDERS: Dict[str, Dict[str, str]] = {
        "openrouter": {
            "base_url_attr": "OPENROUTER_BASE_URL",
            "api_key_attr":  "OPENROUTER_API_KEY",
            "timeout_attr":  "OPENROUTER_TIMEOUT",
            "extra_headers_method": "_openrouter_extra_headers",
        },
        "groq": {
            "base_url_attr": "GROQ_BASE_URL",
            "api_key_attr":  "GROQ_API_KEY",
            "timeout_attr":  "GROQ_TIMEOUT",
            "extra_headers_method": "",
        },
        "gemini": {
            "base_url_attr": "GEMINI_BASE_URL",
            "api_key_attr":  "GEMINI_API_KEY",
            "timeout_attr":  "GEMINI_TIMEOUT",
            "extra_headers_method": "",
        },
    }

    @classmethod
    def _provider_and_model(cls, model: str) -> Tuple[str, str]:
        """Split a routed model name into (provider, real_model_name).

        Names prefixed with a known provider go to that cloud service (prefix
        stripped); everything else is a local Ollama model.
        """
        if not model:
            return "ollama", model
        for provider in cls.OPENAI_COMPAT_PROVIDERS:
            prefix = provider + ":"
            if model.startswith(prefix):
                return provider, model[len(prefix):]
        return "ollama", model

    @staticmethod
    def _openrouter_extra_headers() -> Dict[str, str]:
        # OpenRouter uses these for dashboard attribution; harmless if unset.
        return {
            "HTTP-Referer": settings.OPENROUTER_SITE_URL,
            "X-Title": settings.OPENROUTER_APP_NAME,
        }

    def _provider_request_config(self, provider: str) -> Tuple[str, str, int, Dict[str, str]]:
        """Look up (base_url, api_key, timeout, extra_headers) for an OpenAI-compatible provider.

        Raises RuntimeError if the API key isn't configured — the caller should
        translate that into an "error" event or move on to the next fallback.
        """
        cfg = self.OPENAI_COMPAT_PROVIDERS.get(provider)
        if not cfg:
            raise RuntimeError(f"Unknown provider '{provider}'")
        base_url = getattr(settings, cfg["base_url_attr"], "")
        api_key  = getattr(settings, cfg["api_key_attr"], "")
        timeout  = getattr(settings, cfg["timeout_attr"], 60)
        if not api_key:
            raise RuntimeError(f"{cfg['api_key_attr']} is not set")
        extras_method = cfg.get("extra_headers_method") or ""
        extras = getattr(self, extras_method)() if extras_method else {}
        return base_url, api_key, timeout, extras

    def _chat_openai_compat(
        self,
        provider: str,
        model: str,
        messages: List[Dict],
        num_predict: int,
        temperature: float,
    ) -> str:
        """Non-streaming chat via any OpenAI-compatible provider (OpenRouter / Groq / Gemini)."""
        base_url, api_key, timeout, extra_headers = self._provider_request_config(provider)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            **extra_headers,
        }
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": num_predict,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "").strip()

    def _stream_openai_compat(
        self,
        provider: str,
        model: str,
        messages: List[Dict],
        num_predict: int,
        temperature: float,
    ) -> Iterator[Tuple[str, str]]:
        """Streaming chat via any OpenAI-compatible provider. Yields the same
        ('token'/'done') tuples as generate_stream so the caller is provider-agnostic."""
        try:
            base_url, api_key, timeout, extra_headers = self._provider_request_config(provider)
        except RuntimeError as e:
            yield "error", str(e)
            return
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            **extra_headers,
        }
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "temperature": temperature,
                "max_tokens": num_predict,
            },
            timeout=timeout,
            stream=True,
        )
        response.raise_for_status()

        buffer: List[str] = []
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            piece = (choices[0].get("delta") or {}).get("content", "")
            if piece:
                buffer.append(piece)
                yield "token", piece

        full = self._strip_thinking("".join(buffer)).strip()
        yield "done", full

    # Back-compat shims — the rest of the engine still imports these names.
    def _chat_openrouter(self, model, messages, num_predict, temperature):
        return self._chat_openai_compat("openrouter", model, messages, num_predict, temperature)

    def _stream_openrouter(self, model, messages, num_predict, temperature):
        yield from self._stream_openai_compat("openrouter", model, messages, num_predict, temperature)

    SUMMARIZER_PROMPT = (
        "You are a faithful conversation summarizer for a personal assistant.\n\n"
        "STRICT RULES:\n"
        "1. Include ONLY facts, decisions, and topics explicitly stated in the exchanges. "
        "Never add new information, advice, opinions, or guesses.\n"
        "2. Use neutral third-person voice. Example: 'The user asked about X. The assistant "
        "explained Y.'\n"
        "3. Output 4-8 short bullet points OR one tight paragraph — whichever is cleaner.\n"
        "4. Hard limit: 150 words.\n"
        "5. Preserve specific names, numbers, decisions, and preferences verbatim.\n"
        "6. If a prior summary is provided, MERGE it with the new exchanges into one coherent "
        "summary. Drop nothing important. Do not duplicate facts.\n"
        "7. If a phrase looks like a speech-to-text mishearing (e.g. 'chart' for 'chat', "
        "'Samarit' for 'summarize'), summarize the intended meaning, not the literal error.\n"
        "8. Do NOT include reasoning, <think> tags, emojis, headers like 'Summary:', or any "
        "preamble. Output the summary text only."
    )

    def summarize_text(self, exchanges_text: str, prior_summary: str = "") -> str:
        """Compress past chat exchanges (and an optional prior summary) into a brief recap.

        Used by ConversationMemory to fold the oldest 10 exchanges into a rolling summary
        once the verbatim buffer is full.

        Routes through the same multi-provider fallback chain `/chat` uses
        (`_chat_ollama` → OpenRouter / Groq / Gemini / local Ollama). The
        "fast" role is used because summarization is a cheap compression task
        and we don't want to burn brain-tier tokens on it. Previously this
        called Ollama directly via `{self.base_url}/api/chat`, which broke
        any deployment with Ollama disabled (Docker, Render, anywhere
        `OLLAMA_BASE_URL=""`) — the URL became literally `'/api/chat'` and
        the summary silently failed every 10 turns.
        """
        try:
            prior_block = (
                f"[Prior summary]\n{prior_summary.strip()}\n\n"
                if prior_summary and prior_summary.strip()
                else ""
            )
            user_payload = (
                f"{prior_block}"
                f"[New exchanges to fold in]\n{exchanges_text.strip()}\n\n"
                "[Output: the merged summary only, no preamble]"
            )
            messages = [
                {"role": "system", "content": self.SUMMARIZER_PROMPT},
                {"role": "user", "content": user_payload},
            ]

            # Pick a model via the router — "fast" role keeps summarizer cheap.
            # Lazy import to avoid circular dependency at module load.
            try:
                from brain.model_router import get_model_router
                router = get_model_router(self)
                model_override = router.pick_model(intent=None, voice_mode=False, explicit_role="fast")
            except Exception as e:
                logger.warning(f"Summarizer: router unavailable ({e}); using engine default model")
                model_override = self.model_name

            content = self._chat_ollama(
                messages,
                voice_mode=False,
                num_predict_override=400,
                temperature_override=0.2,
                model_override=model_override,
            )
            return self._strip_thinking(content)
        except requests.Timeout:
            logger.error("Summarization timed out — keeping prior summary unchanged")
            return prior_summary
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return prior_summary
    
    def chat_with_memory(self, prompt: str, conversation_history: List[Dict]) -> str:
        """
        Generate response with conversation history
        
        Args:
            prompt: Current user prompt
            conversation_history: List of previous messages
            
        Returns:
            Generated response
        """
        # Build context from history
        history_text = "\n".join([
            f"User: {msg['user']}\nAssistant: {msg['assistant']}"
            for msg in conversation_history[-5:]  # Last 5 exchanges
        ])
        
        context = f"Conversation History:\n{history_text}" if history_text else ""
        return self.generate(prompt, context)
    
    def stream_response(self, prompt: str) -> str:
        """
        Generate streaming response (real-time output)
        
        Args:
            prompt: The user prompt
            
        Returns:
            Generated response (streaming handled by callback)
        """
        return self.generate(prompt)
    
    def _ollama_disabled(self) -> bool:
        """Whether the Ollama probe should be skipped entirely.

        True when no base_url is configured (cloud deploys with `OLLAMA_BASE_URL=""`)
        or when the URL is malformed (no scheme). Either case used to spam the log
        with "Invalid URL '/api/tags'" every 5 seconds because the legacy UI polls
        /status; on Render that meant pages of meaningless errors. Returning True
        from here short-circuits the probe so nothing is logged.
        """
        url = (self.base_url or "").strip()
        if not url:
            return True
        if not (url.startswith("http://") or url.startswith("https://")):
            return True
        return False

    def check_connection(self) -> bool:
        """Check if Ollama is running and accessible.

        Returns False (silently) when no Ollama URL is configured — cloud-only
        deploys don't run Ollama and shouldn't log connection errors for it."""
        if self._ollama_disabled():
            return False
        try:
            import requests
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Ollama probe failed at {self.base_url}: {e}")
            return False

    def set_model(self, model_name: str):
        """Switch to a different model"""
        self.model_name = model_name
        logger.info(f"Switched to model: {model_name}")

    def get_available_models(self) -> List[str]:
        """Get list of models available in Ollama.

        Returns [] (silently) when no Ollama URL is configured — same reason as
        check_connection above. Cloud deploys spam the log without this guard."""
        if self._ollama_disabled():
            return []
        try:
            import requests
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = [model["name"] for model in response.json().get("models", [])]
                logger.info(f"Available models: {models}")
                return models
            return []
        except Exception as e:
            logger.warning(f"Ollama model list failed at {self.base_url}: {e}")
            return []


# Global instance
_llm_engine = None


def get_llm_engine() -> LLMEngine:
    """Get or create the global LLM engine instance"""
    global _llm_engine
    if _llm_engine is None:
        _llm_engine = LLMEngine()
    return _llm_engine
