"""
Context Manager - Handles context and reasoning for Jarvis
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ContextManager:
    """Manages conversation context and current state"""
    
    def __init__(self):
        self.current_context = {}
        self.conversation_history = []
        self.active_tasks = []
        self.current_user = "User"
        self.session_start = datetime.now()
    
    def add_to_context(self, key: str, value: any):
        """Add information to current context"""
        self.current_context[key] = value
        logger.debug(f"Added to context: {key}")
    
    def get_context(self, key: str) -> Optional[any]:
        """Retrieve context value"""
        return self.current_context.get(key)
    
    def add_to_history(self, user_input: str, assistant_response: str):
        """Add exchange to conversation history"""
        self.conversation_history.append({
            "timestamp": datetime.now().isoformat(),
            "user": user_input,
            "assistant": assistant_response
        })
        logger.debug(f"Added to history: User message with {len(assistant_response)} chars")
    
    def get_history(self, limit: int = 10) -> List[Dict]:
        """Get recent conversation history"""
        return self.conversation_history[-limit:]
    
    def get_context_string(self) -> str:
        """Get formatted context for LLM"""
        context_parts = []
        
        if self.current_context:
            context_parts.append("Current Context:")
            for key, value in self.current_context.items():
                context_parts.append(f"- {key}: {value}")
        
        if self.active_tasks:
            context_parts.append("\nActive Tasks:")
            for task in self.active_tasks:
                context_parts.append(f"- {task}")
        
        return "\n".join(context_parts) if context_parts else ""
    
    def clear_context(self):
        """Clear current context"""
        self.current_context.clear()
        logger.info("Context cleared")
    
    def reset_session(self):
        """Reset entire session"""
        self.current_context.clear()
        self.conversation_history.clear()
        self.active_tasks.clear()
        self.session_start = datetime.now()
        logger.info("Session reset")


class Reasoning:
    """Handles reasoning and decision making"""
    
    def __init__(self, context_manager: ContextManager):
        self.context = context_manager
    
    def analyze_intent(self, user_input: str) -> Dict:
        """Analyze user intent from input.

        Order matters: code_help is checked first because generic words like
        "open" and "start" would otherwise mis-route a request such as
        "open a file in Python and write a Flask API" to open_app.

        Short messages (<= 12 chars, e.g. "hi", "thanks", "ok"): treated as
        small_talk so they route to the fast model instead of waking the
        reasoning model for a one-word greeting.
        """
        intent_lower = user_input.lower().strip()

        # Greetings, acknowledgements, and other ultra-short asks bypass the
        # heavy router and always go to fast. This is the bug that made "hi"
        # spend 122 s in DeepSeek-R1's think phase.
        # Strip trailing punctuation so "Hello.", "Hi!", "Hey?" all qualify.
        short_test = intent_lower.rstrip(" .!?,;:")
        if len(short_test) <= 14:
            short_signals = ("hi", "hello", "hey", "yo", "hola", "thanks", "thank you",
                             "ok", "okay", "yes", "no", "sure", "cool", "great",
                             "got it", "k", "kk", "bye", "goodbye",
                             "good morning", "good night", "good evening",
                             "morning", "evening", "howdy", "sup")
            if short_test in short_signals or len(short_test) <= 4:
                logger.debug(f"Short-message fast-path matched ({short_test!r})")
                return {
                    "primary_intent": "small_talk",
                    "secondary_intents": [],
                    "confidence": 1.0,
                }

        intents = {
            # Voice macros (checked first so they short-circuit the LLM
            # pipeline entirely). Each maps to a recipe in brain/macro_runner.py.
            # Keep phrase lists tight — false positives here mean the chat
            # silently turns into a deterministic script instead of an LLM
            # reply, which is more confusing than a slow brain response.
            "morning_brief": [
                "morning brief", "morning briefing",
                "brief me", "give me my brief", "give me a brief",
                "what's on my plate", "whats on my plate",
                "what's on the agenda", "whats on the agenda",
                "good morning hunt", "good morning doondy",
                "daily standup",
            ],
            "read_open_tasks": [
                "read my tasks", "read my open tasks", "list my tasks",
                "list my open tasks", "list open tasks",
                "what are my tasks", "what's left", "whats left",
                "what's pending", "whats pending",
                "what's still open", "whats still open",
            ],
            "wrap_up_session": [
                "wrap up the session", "wrap up this session",
                "wrap up the chat", "wrap up",
                "end the session", "end this session",
                "sign off for today", "sign off for the day",
                "close out the session",
            ],

            # Workspace awareness — pure desktop probe, no LLM call. Phrase
            # list intentionally tight so casual chat ("what am I doing wrong?")
            # doesn't accidentally trigger a workspace dump.
            "workspace_query": [
                "what am i working on",
                "what am i doing right now",
                "what windows are open",
                "what's on my screen", "whats on my screen",
                "what apps are open",
                "what's open on my desktop", "whats open on my desktop",
                "show me my workspace", "show my workspace",
                "what's my active window", "whats my active window",
                "describe my workspace",
            ],
            "read_clipboard": [
                "what's in my clipboard", "whats in my clipboard",
                "read my clipboard", "read clipboard",
                "what's on my clipboard", "whats on my clipboard",
                "show me my clipboard", "show my clipboard",
                "clipboard contents",
                "what did i copy",
            ],

            # Project context switch — "continue X" / "switch to X" / "work
            # on X". The router doesn't route on this intent; the chat
            # handler reads it, looks the project up in ProjectStore, and
            # activates it on the current session before generating the
            # reply. Kept narrow so casual "switch the lights" or "continue
            # talking" don't trigger.
            "project_continue": [
                "continue my", "continue the", "continue working on",
                "resume my", "resume the",
                "switch to project", "switch to my",
                "open project", "load project",
                "work on my", "work on the",
                "back to my", "back to the",
            ],

            # Explicit reasoning asks → brain (DeepSeek-R1). Kept narrow so
            # casual chat doesn't accidentally trigger the slow path.
            "reasoning": [
                "step by step", "step-by-step",
                "analyze", "analyse",
                "compare and contrast", "compare", "contrast",
                "evaluate", "weigh the",
                "think through", "reason through", "reason about",
                "in detail", "in depth",
                "plan a", "plan out", "strategy for", "strategize",
                "deduce", "infer", "derive",
                "solve this problem", "math problem", "prove that",
                "trade-offs", "tradeoffs",
                "pros and cons",
                "long answer", "detailed explanation",
            ],

            # Code / dev tasks. Includes language names, framework names, and
            # common dev verbs so requests like "Write a Flask API" or
            # "implement a SQL query" route to the coder role.
            "code_help": [
                # explicit
                "code", "debug", "error", "fix", "bug", "exception",
                "traceback", "stack trace", "syntax", "refactor", "implement",
                # structures
                "function", "class", "method", "script", "module",
                "endpoint", "route", "api", "regex", "algorithm",
                # languages
                "python", "javascript", "typescript", "java ",  # trailing space avoids "javanese"
                "c++", "c#", "rust", "ruby", "kotlin", "swift", "golang",
                "html", "css", "sql", "json",
                # frameworks / libs
                "flask", "django", "fastapi", "express", "react",
                "vue", "angular", "nextjs", "next.js", "node.js", "nodejs",
                "tailwind", "pandas", "numpy", "tensorflow", "pytorch",
                # ops
                "compile", "deploy", "docker", "kubernetes", "git ", "github",
                # write-pattern (very common ask: "write a … function/api/script")
                "write a ", "write me ",
            ],
            "open_app":       ["open", "launch", "start"],
            "close_app":      ["close", "quit", "exit", "shutdown"],
            "search":         ["search", "find", "look for", "what is"],
            "file_operation": ["create file", "delete file", "move file", "copy file",
                               "rename file", "open file"],
            "conversation":   ["how are you", "tell me", "explain"],
            "task":           ["do this", "can you", "please"],
        }

        detected_intents = []
        for intent_type, keywords in intents.items():
            if any(keyword in intent_lower for keyword in keywords):
                detected_intents.append(intent_type)

        logger.debug(f"Detected intents: {detected_intents}")

        return {
            "primary_intent": detected_intents[0] if detected_intents else "conversation",
            "secondary_intents": detected_intents[1:] if len(detected_intents) > 1 else [],
            "confidence": len(detected_intents) / len(intents)
        }
    
    def decide_action(self, intent: Dict) -> str:
        """Decide which agent/action to use based on intent"""
        primary = intent.get("primary_intent", "conversation")
        
        action_map = {
            "open_app": "desktop_automation",
            "close_app": "desktop_automation",
            "search": "research_agent",
            "file_operation": "file_manager",
            "code_help": "coding_agent",
            "conversation": "chat_engine",
            "task": "task_planner",
        }
        
        action = action_map.get(primary, "chat_engine")
        logger.info(f"Decided action: {action}")
        return action


# Global instances
_context_manager = None
_reasoning = None


def get_context_manager() -> ContextManager:
    """Get or create global context manager"""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager


def get_reasoning() -> Reasoning:
    """Get or create global reasoning engine"""
    global _reasoning
    if _reasoning is None:
        _reasoning = Reasoning(get_context_manager())
    return _reasoning
