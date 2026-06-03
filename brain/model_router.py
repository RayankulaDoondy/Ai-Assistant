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
from typing import Dict, Optional

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
    "small_talk":     "fast",
    "conversation":   "fast",
    "task":           "fast",
}

DEFAULT_ROLE = "fast"
KNOWN_ROLES = ("brain", "coder", "fast", "vision")


class ModelRouter:
    """Maintains the role→model map and picks a concrete model per request."""

    def __init__(self, llm_engine):
        self.llm_engine = llm_engine
        self._roles: Dict[str, str] = self._load_default_roles()
        # Cache the available-models list briefly so we don't hit Ollama once
        # per chat turn just to check what's pulled.
        self._available_cache: Optional[set] = None
        self._available_cache_hits = 0
        self._available_cache_max_hits = 25

    @staticmethod
    def _load_default_roles() -> Dict[str, str]:
        try:
            data = json.loads(settings.LLM_ROLE_MODELS_JSON)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if k in KNOWN_ROLES and v}
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Could not parse LLM_ROLE_MODELS_JSON ({e}); using empty role map")
        return {}

    def get_roles(self) -> Dict[str, str]:
        return dict(self._roles)

    def set_roles(self, roles: Dict[str, str]) -> Dict[str, str]:
        """Replace the role map (runtime only — restart restores from settings)."""
        cleaned = {k: v for k, v in (roles or {}).items() if k in KNOWN_ROLES and v}
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

    @staticmethod
    def _is_available(model: str, available: set) -> bool:
        """Whether a routed model can actually be used right now.

        OpenRouter models (prefixed "openrouter:") are usable as long as an API
        key is configured — they never appear in Ollama's local tag list. Local
        models are usable if pulled, or if we couldn't read the tag list at all
        (empty `available` means "assume present" rather than block everything).
        """
        if model.startswith("openrouter:"):
            return bool(getattr(settings, "OPENROUTER_API_KEY", ""))
        return (not available) or (model in available)

    def pick_model(
        self,
        intent: Optional[str],
        voice_mode: bool = False,
        explicit_role: Optional[str] = None,
    ) -> str:
        """Return the model name to use for one /chat call.

        Resolution order:
          1. If routing is disabled, return the engine's current default.
          2. Pick a role: explicit override > voice → fast > intent → role.
          3. If that role's model is pulled in Ollama, use it.
          4. Otherwise fall back to settings.LLM_ROLE_FALLBACK's model.
          5. Otherwise fall back to the engine's current default model.
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

        preferred = self._roles.get(role)
        if preferred and self._is_available(preferred, available):
            return preferred

        fallback_role = settings.LLM_ROLE_FALLBACK
        fallback_model = self._roles.get(fallback_role)
        if fallback_model and self._is_available(fallback_model, available):
            if preferred:
                logger.info(
                    f"Role '{role}' wants '{preferred}' which is not pulled; "
                    f"falling back to '{fallback_role}' → '{fallback_model}'"
                )
            return fallback_model

        # Final fallback: whatever the engine was configured with at startup.
        logger.info(
            f"No usable model for role '{role}' (preferred={preferred}); "
            f"using engine default '{self.llm_engine.model_name}'"
        )
        return self.llm_engine.model_name


_router: Optional[ModelRouter] = None


def get_model_router(llm_engine) -> ModelRouter:
    """Get or create the global router. Rebuilds if a different engine is passed."""
    global _router
    if _router is None or _router.llm_engine is not llm_engine:
        _router = ModelRouter(llm_engine)
    return _router
