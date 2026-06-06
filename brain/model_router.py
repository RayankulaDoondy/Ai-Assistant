"""Intent-aware model router for the Jarvis brain.

Picks the right Ollama model for each request based on the detected intent
(or an explicit role override). Falls back gracefully when the preferred
model isn't pulled, so a half-installed Ollama setup never crashes the chat.

Roles:
    brain   — deep reasoning, planning, conversation        (e.g. DeepSeek R1)
    coder   — code generation, debugging                    (e.g. Qwen2.5-Coder)
    fast    — quick replies, voice mode, simple commands    (e.g. Qwen2.5 0.5B)
    vision  — image / screenshot understanding              (e.g. LLaVA)
"""
import json
import logging
from typing import Dict, List, Optional, Union

from config import settings

logger = logging.getLogger(__name__)


# Intent labels (from Reasoning.analyze_intent) → role. Voice mode always
# overrides this and uses the "fast" role for minimum latency.
#
# Design rule: the heavy `brain` (reasoning) model is opt-in. Generic chat,
# greetings, and routine tasks go to `fast` — answering "hi" with a 5 GB
# reasoning model is what made the assistant feel broken. Brain is only
# invoked when the user explicitly signals they want deep thinking via the
# `reasoning` intent (analyze/compare/plan/step-by-step/etc.) or an explicit
# role override (e.g. the "Plan a beach trip" quick chip).
INTENT_TO_ROLE: Dict[str, str] = {
    "open_app":       "fast",
    "close_app":      "fast",
    "file_operation": "fast",
    "search":         "fast",
    "code_help":      "coder",
    "reasoning":      "brain",
    # Memory-grounded recall ("tell me about the X we wrote", "remind me
    # of Y") — uses the conversational brain persona, not the coder persona,
    # so a query about previously-written code is answered as a synthesis
    # of past context rather than a fresh "write code now" request.
    "recall":         "brain",
    "small_talk":     "fast",
    "conversation":   "fast",
    "task":           "fast",
}

DEFAULT_ROLE = "fast"
KNOWN_ROLES = ("brain", "coder", "fast", "vision")


RoleValue = Union[str, List[str]]  # either a single model or an ordered fallback chain


class ModelRouter:
    """Maintains the role→model(s) map and picks a model (or chain) per request.

    Each role can map to either:
      - a single model name (string)            — back-compat with old configs
      - a list of model names (fallback chain)  — try in order; first that works wins
    """

    def __init__(self, llm_engine):
        self.llm_engine = llm_engine
        self._roles: Dict[str, RoleValue] = self._load_default_roles()
        # Cache the available-models list briefly so we don't hit Ollama once
        # per chat turn just to check what's pulled.
        self._available_cache: Optional[set] = None
        self._available_cache_hits = 0
        self._available_cache_max_hits = 25

    @staticmethod
    def _normalize_role_value(v) -> Optional[RoleValue]:
        """Accept either a string or a non-empty list of strings; reject everything else."""
        if isinstance(v, str) and v:
            return v
        if isinstance(v, (list, tuple)):
            cleaned = [str(item) for item in v if isinstance(item, str) and item]
            return cleaned if cleaned else None
        return None

    @classmethod
    def _load_default_roles(cls) -> Dict[str, RoleValue]:
        try:
            data = json.loads(settings.LLM_ROLE_MODELS_JSON)
            if isinstance(data, dict):
                out: Dict[str, RoleValue] = {}
                for k, v in data.items():
                    if k not in KNOWN_ROLES:
                        continue
                    norm = cls._normalize_role_value(v)
                    if norm is not None:
                        out[k] = norm
                return out
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Could not parse LLM_ROLE_MODELS_JSON ({e}); using empty role map")
        return {}

    def get_roles(self) -> Dict[str, RoleValue]:
        return dict(self._roles)

    def set_roles(self, roles: Dict[str, RoleValue]) -> Dict[str, RoleValue]:
        """Replace the role map (runtime only — restart restores from settings)."""
        cleaned: Dict[str, RoleValue] = {}
        for k, v in (roles or {}).items():
            if k not in KNOWN_ROLES:
                continue
            norm = self._normalize_role_value(v)
            if norm is not None:
                cleaned[k] = norm
        self._roles = cleaned
        # Force re-check of available models on next pick.
        self._available_cache = None
        return dict(self._roles)

    def _available_models(self) -> set:
        if self._available_cache is not None and self._available_cache_hits < self._available_cache_max_hits:
            self._available_cache_hits += 1
            return self._available_cache
        try:
            models = set(self.llm_engine.get_available_models())
        except Exception as e:
            logger.warning(f"get_available_models failed: {e}; assuming all pulled")
            models = set()
        self._available_cache = models
        self._available_cache_hits = 0
        return models

    # Known provider prefixes (must match brain.llm_engine.LLMEngine.OPENAI_COMPAT_PROVIDERS).
    _PROVIDER_KEY_ATTR = {
        "openrouter": "OPENROUTER_API_KEY",
        "groq":       "GROQ_API_KEY",
        "gemini":     "GEMINI_API_KEY",
    }

    @classmethod
    def _is_available(cls, model: str, available: set) -> bool:
        """Whether a routed model can actually be used right now.

        Cloud models (prefixed with a known provider name) are usable as long as
        that provider's API key is set — they never appear in Ollama's local tag
        list. Local models are usable if pulled, or if we couldn't read the tag
        list at all (empty `available` means "assume present" rather than block
        everything).
        """
        for prefix, key_attr in cls._PROVIDER_KEY_ATTR.items():
            if model.startswith(prefix + ":"):
                return bool(getattr(settings, key_attr, ""))
        return (not available) or (model in available)

    @classmethod
    def _filter_available_chain(cls, value: RoleValue, available: set) -> List[str]:
        """Take a string or list role value, return the subset that's actually usable now."""
        if isinstance(value, str):
            return [value] if cls._is_available(value, available) else []
        return [m for m in value if cls._is_available(m, available)]

    def pick_model(
        self,
        intent: Optional[str],
        voice_mode: bool = False,
        explicit_role: Optional[str] = None,
    ) -> Union[str, List[str]]:
        """Return the model (or fallback chain) to use for one /chat call.

        Backward compatible: returns a single string when only one candidate is
        usable, or when the role config was a single string. Returns a list when
        multiple fallback models are configured AND usable — the LLM engine will
        try them in order.
        """
        if not getattr(settings, "LLM_ROUTING_ENABLED", True):
            return self.llm_engine.model_name

        if explicit_role and explicit_role in KNOWN_ROLES:
            role = explicit_role
        elif voice_mode:
            role = "fast"
        else:
            role = INTENT_TO_ROLE.get((intent or "").lower(), DEFAULT_ROLE)

        available = self._available_models()
        # Build the ordered candidate list: the role's chain, then the fallback
        # role's chain, then the engine default — de-duplicated, preserving order.
        candidates: List[str] = []
        seen = set()

        def _extend(value: Optional[RoleValue]):
            if value is None:
                return
            for m in self._filter_available_chain(value, available):
                if m not in seen:
                    candidates.append(m)
                    seen.add(m)

        _extend(self._roles.get(role))
        if role != settings.LLM_ROLE_FALLBACK:
            _extend(self._roles.get(settings.LLM_ROLE_FALLBACK))
        if self.llm_engine.model_name and self.llm_engine.model_name not in seen:
            candidates.append(self.llm_engine.model_name)

        if not candidates:
            logger.info(f"No usable model for role '{role}'; using engine default '{self.llm_engine.model_name}'")
            return self.llm_engine.model_name
        if len(candidates) == 1:
            return candidates[0]
        return candidates


_router: Optional[ModelRouter] = None


def get_model_router(llm_engine) -> ModelRouter:
    """Get or create the global router. Rebuilds if a different engine is passed."""
    global _router
    if _router is None or _router.llm_engine is not llm_engine:
        _router = ModelRouter(llm_engine)
    return _router
