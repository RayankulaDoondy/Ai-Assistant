"""
Memory System - Vector-based semantic memory using ChromaDB
"""
import json
import logging
import os
import tempfile
import uuid
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)


class MemoryStore:
    """Vector memory store using ChromaDB"""
    
    def __init__(self, persist_dir: str = "./data/chroma", embeddings_model: str = "all-MiniLM-L6-v2"):
        """
        Initialize memory store
        
        Args:
            persist_dir: Directory to persist data
            embeddings_model: Sentence transformer model for embeddings
        """
        try:
            import chromadb
            from chromadb.config import Settings
            
            self.chroma_client = chromadb
            self.persist_dir = persist_dir
            self.embeddings_model = embeddings_model
            
            # Create persist directory if not exists
            os.makedirs(persist_dir, exist_ok=True)
            
            # Initialize Chroma client with persistence using the new client settings
            # Use is_persistent=True and provide the persist directory. This avoids
            # deprecated constructor arguments like `chroma_db_impl`.
            self.client = chromadb.Client(
                Settings(
                    is_persistent=True,
                    persist_directory=persist_dir,
                    anonymized_telemetry=False
                )
            )
            
            # Get or create collection
            self.collection = self.client.get_or_create_collection(
                name="jarvis_memory",
                metadata={"hnsw:space": "cosine"}
            )
            
            logger.info(f"Memory store initialized at {persist_dir}")
        except ImportError:
            logger.error("ChromaDB not installed")
            raise
    
    def store_memory(self, content: str, memory_type: str = "general", metadata: Dict = None) -> str:
        """
        Store a memory item
        
        Args:
            content: Memory content
            memory_type: Type of memory (conversation, project, task, preference, etc.)
            metadata: Additional metadata
            
        Returns:
            Memory ID
        """
        try:
            import uuid
            
            memory_id = str(uuid.uuid4())
            
            if metadata is None:
                metadata = {}
            
            metadata["type"] = memory_type
            metadata["timestamp"] = datetime.now().isoformat()
            
            self.collection.add(
                ids=[memory_id],
                documents=[content],
                metadatas=[metadata]
            )
            
            logger.info(f"Stored memory: {memory_id} (type: {memory_type})")
            return memory_id
        except Exception as e:
            logger.error(f"Error storing memory: {str(e)}")
            return ""
    
    def retrieve_memories(self, query: str, limit: int = 3, memory_type: Optional[str] = None) -> List[Dict]:
        """
        Retrieve relevant memories using semantic search
        
        Args:
            query: Search query
            limit: Number of results to return
            memory_type: Filter by memory type
            
        Returns:
            List of relevant memories
        """
        try:
            where_filter = {"type": memory_type} if memory_type else None
            
            results = self.collection.query(
                query_texts=[query],
                n_results=limit,
                where=where_filter
            )
            
            memories = []
            if results and results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    memory = {
                        "content": doc,
                        "id": results["ids"][0][i] if results["ids"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else 0
                    }
                    memories.append(memory)
            
            logger.debug(f"Retrieved {len(memories)} memories for query: {query[:50]}")
            return memories
        except Exception as e:
            logger.error(f"Error retrieving memories: {str(e)}")
            return []
    
    def update_memory(self, memory_id: str, content: str, metadata: Dict = None) -> bool:
        """Update a memory item"""
        try:
            if metadata is None:
                metadata = {}
            metadata["updated"] = datetime.now().isoformat()
            
            self.collection.update(
                ids=[memory_id],
                documents=[content],
                metadatas=[metadata]
            )
            logger.info(f"Updated memory: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating memory: {str(e)}")
            return False
    
    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory item"""
        try:
            self.collection.delete(ids=[memory_id])
            logger.info(f"Deleted memory: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting memory: {str(e)}")
            return False
    
    def store_profile_fact(self, fact: str) -> Optional[str]:
        """Store a durable user profile fact, skipping near-duplicates."""
        clean = (fact or "").strip()
        if not clean:
            return None
        # Cheap duplicate guard: if a profile memory with the exact same text
        # already exists, skip the write so the same fact doesn't accumulate.
        existing = self.get_all_memories(memory_type="profile")
        for item in existing:
            if (item.get("content") or "").strip().lower() == clean.lower():
                return item.get("id")
        memory_id = self.store_memory(content=clean, memory_type="profile")
        # Phase D: best-effort mirror to Mongo.
        try:
            from .mongo_sync import mongo_sync_singleton
            m = mongo_sync_singleton()
            if m and m.available:
                m.add_fact(clean)
        except Exception as e:
            logger.warning(f"Mongo fact sync failed (non-fatal): {e}")
        return memory_id

    def get_profile_facts(self, limit: int = 30) -> List[str]:
        """Return all stored profile facts as plain strings, newest first."""
        items = self.get_all_memories(memory_type="profile")
        # Sort newest first by stored timestamp when available.
        def _ts(item):
            return (item.get("metadata") or {}).get("timestamp", "")
        items.sort(key=_ts, reverse=True)
        facts = [(item.get("content") or "").strip() for item in items if item.get("content")]
        return facts[:limit]

    def get_all_memories(self, memory_type: Optional[str] = None, limit: int = None) -> List[Dict]:
        """Get all memories, optionally filtered by type"""
        try:
            where_filter = {"type": memory_type} if memory_type else None
            
            results = self.collection.get(where=where_filter)
            
            memories = []
            if results and results["documents"]:
                for i, doc in enumerate(results["documents"]):
                    memory = {
                        "content": doc,
                        "id": results["ids"][i] if results["ids"] else "",
                        "metadata": results["metadatas"][i] if results["metadatas"] else {},
                    }
                    memories.append(memory)
            
            if limit:
                memories = memories[:limit]
            
            logger.info(f"Retrieved {len(memories)} memories")
            return memories
        except Exception as e:
            logger.error(f"Error getting all memories: {str(e)}")
            return []


class ConversationMemory:
    """Manages the running thread of the current chat session.

    Maintains:
      - `current_session`: verbatim ring buffer of the most recent exchanges.
      - `rolling_summary`: a faithful summary of older exchanges that have already
        been folded out of the verbatim buffer.

    Compaction rule: keep up to MAX_VERBATIM_EXCHANGES (default 10) exchanges
    verbatim. When the 11th exchange arrives, the original 10 are compressed by
    the LLM into the rolling_summary (merging with any prior summary), and only
    the newest exchange remains in the verbatim buffer. This bounds context size
    while preserving continuity across long chats.
    """

    MAX_VERBATIM_EXCHANGES = 10
    SCHEMA_VERSION = 3  # bumped: now includes active_project_id

    def __init__(
        self,
        memory_store: MemoryStore,
        llm_engine=None,
        persist_path: Optional[str] = None,
    ):
        self.memory_store = memory_store
        self.llm_engine = llm_engine  # set later via set_llm_engine() to avoid circular init
        self.current_session: List[Dict[str, str]] = []
        self.rolling_summary: str = ""
        # Phase D: session identity for Mongo upserts and the Sessions sidebar.
        # Generated on first construction; preserved across restarts via the
        # persist file; regenerated on clear_session().
        self.session_id: str = uuid.uuid4().hex
        self.session_title: Optional[str] = None
        self.session_started_at: str = datetime.now().isoformat(timespec="seconds")
        # Phase E1: active project id (per-session). Set by the chat handler
        # when the user picks one from the UI or triggers project_continue.
        # None = no active project (Hunt behaves as before).
        self.active_project_id: Optional[str] = None
        # Disk persistence. None = ephemeral (in-memory only).
        self.persist_path: Optional[str] = persist_path
        if persist_path:
            self._load_from_disk()

    def set_llm_engine(self, llm_engine) -> None:
        """Late-bind the LLM engine used for summarization."""
        self.llm_engine = llm_engine

    def _load_from_disk(self) -> None:
        """Restore the running thread + session metadata from the persist file."""
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            self.current_session = [
                t for t in data.get("recent", []) if isinstance(t, dict)
            ]
            self.rolling_summary = (data.get("rolling_summary") or "").strip()
            # Phase D: restore session identity. Old files (schema=1) won't
            # have these, so we keep the freshly generated defaults.
            if data.get("session_id"):
                self.session_id = data["session_id"]
            if data.get("session_title"):
                self.session_title = data["session_title"]
            if data.get("session_started_at"):
                self.session_started_at = data["session_started_at"]
            # Phase E1: restore active project (schema=3+).
            if data.get("active_project_id"):
                self.active_project_id = data["active_project_id"]
            logger.info(
                f"Loaded persisted session {self.session_id[:8]}…: "
                f"{len(self.current_session)} verbatim turns, "
                f"summary={'yes' if self.rolling_summary else 'empty'}"
            )
        except Exception as e:
            logger.warning(f"Failed to load persisted session ({self.persist_path}): {e}")

    def _save_to_disk(self) -> None:
        """Atomically write the current state to the persist file. Best-effort."""
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
            payload = {
                "schema": self.SCHEMA_VERSION,
                "saved_at": datetime.now().isoformat(),
                "session_id": self.session_id,
                "session_title": self.session_title,
                "session_started_at": self.session_started_at,
                "active_project_id": self.active_project_id,
                "rolling_summary": self.rolling_summary,
                "recent": self.current_session,
            }
            # Atomic write: tmp file in the same dir, then os.replace.
            dir_ = os.path.dirname(self.persist_path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.persist_path)
        except Exception as e:
            logger.warning(f"Failed to persist session ({self.persist_path}): {e}")

    def add_exchange(self, user_input: str, assistant_response: str) -> str:
        """Add a new exchange. Triggers compaction once the verbatim buffer is full."""
        content = f"User: {user_input}\nAssistant: {assistant_response}"

        memory_id = self.memory_store.store_memory(
            content=content,
            memory_type="conversation",
            metadata={
                "user_query": user_input[:100],
                "response_length": len(assistant_response)
            }
        )

        self.current_session.append({
            "user": user_input,
            "assistant": assistant_response,
            "id": memory_id
        })

        # Phase D: derive title from first user message so the Sessions
        # sidebar has something better than the UUID to show.
        if not self.session_title and user_input:
            self.session_title = user_input.strip().split("\n")[0][:80]

        # When the 11th (or later) exchange arrives, fold the oldest 10 into the
        # rolling summary and keep only the newer ones verbatim.
        if len(self.current_session) > self.MAX_VERBATIM_EXCHANGES:
            self._compact()

        self._save_to_disk()
        self._sync_to_mongo()
        return memory_id

    def _sync_to_mongo(self) -> None:
        """Best-effort upsert to MongoDB. Silent when Mongo isn't configured."""
        try:
            from .mongo_sync import mongo_sync_singleton
            m = mongo_sync_singleton()
            if not m or not m.available:
                return
            m.upsert_session(self.session_id, {
                "title": self.session_title or "Untitled chat",
                "started_at": self.session_started_at,
                "active_project_id": self.active_project_id,
                "verbatim": self.current_session,
                "rolling_summary": self.rolling_summary,
            })
        except Exception as e:
            logger.warning(f"Mongo session sync failed (non-fatal): {e}")

    def resume_from(self, session_doc: Dict) -> None:
        """Replace the running state with a previously saved session document.

        Used by `POST /sessions/{id}/resume`. The new state is persisted to
        disk immediately so a server restart preserves the resumed session.
        """
        sid = session_doc.get("_id") or session_doc.get("session_id")
        if sid:
            self.session_id = sid
        self.session_title = session_doc.get("title") or self.session_title
        self.session_started_at = (
            session_doc.get("started_at") or self.session_started_at
        )
        verbatim = session_doc.get("verbatim") or session_doc.get("recent") or []
        self.current_session = [t for t in verbatim if isinstance(t, dict)]
        self.rolling_summary = (session_doc.get("rolling_summary") or "").strip()
        self._save_to_disk()
        logger.info(
            f"Resumed session {self.session_id[:8]}…: "
            f"{len(self.current_session)} verbatim turns"
        )

    def _compact(self) -> None:
        """Fold the oldest MAX_VERBATIM_EXCHANGES into the rolling summary."""
        if not self.llm_engine:
            logger.warning("LLM engine not bound; skipping conversation compaction")
            return

        to_summarize = self.current_session[:self.MAX_VERBATIM_EXCHANGES]
        kept = self.current_session[self.MAX_VERBATIM_EXCHANGES:]

        exchanges_text = "\n\n".join(
            f"User: {ex.get('user', '').strip()}\n"
            f"Assistant: {ex.get('assistant', '').strip()}"
            for ex in to_summarize
        )

        try:
            new_summary = self.llm_engine.summarize_text(
                exchanges_text=exchanges_text,
                prior_summary=self.rolling_summary,
            )
            if new_summary and new_summary.strip():
                self.rolling_summary = new_summary.strip()
                self.current_session = kept
                logger.info(
                    f"Compacted {len(to_summarize)} exchanges into rolling summary "
                    f"({len(self.rolling_summary)} chars). Verbatim buffer now: {len(kept)}."
                )
            else:
                logger.warning("Summarizer returned empty output — verbatim buffer left intact")
        except Exception as e:
            logger.error(f"Compaction failed, leaving buffer intact: {e}")
    
    def get_context(self, query: str, limit: int = 5) -> str:
        """Get relevant long-term conversation context via semantic search."""
        relevant_memories = self.memory_store.retrieve_memories(
            query=query,
            limit=limit,
            memory_type="conversation"
        )

        if not relevant_memories:
            return ""

        context_parts = ["Relevant previous conversations:"]
        for memory in relevant_memories:
            context_parts.append(f"- {memory['content'][:200]}...")

        return "\n".join(context_parts)

    def get_session_messages(self, limit: int = 10) -> List[Dict[str, str]]:
        """Return the most recent in-session exchanges as chat-style messages.

        Unlike get_context (semantic search over all stored memory), this preserves
        the *actual* running thread of the current session so the LLM can reference
        prior turns, summarize the chat, follow up on pronouns, etc.
        """
        if not self.current_session:
            return []
        recent = self.current_session[-limit:]
        messages: List[Dict[str, str]] = []
        for turn in recent:
            user_text = (turn.get("user") or "").strip()
            assistant_text = (turn.get("assistant") or "").strip()
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})
        return messages

    def clear_session(self) -> int:
        """Start a fresh chat session.

        Generates a new session_id (the old one is preserved in Mongo as a
        completed chat reachable from the Sessions sidebar) and wipes the
        verbatim buffer + rolling summary. Deletes the persist file so a
        server restart picks up the new fresh state.
        """
        dropped = len(self.current_session)
        self.current_session.clear()
        self.rolling_summary = ""
        self.session_id = uuid.uuid4().hex
        self.session_title = None
        self.session_started_at = datetime.now().isoformat(timespec="seconds")
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                os.remove(self.persist_path)
            except Exception as e:
                logger.warning(f"Could not remove persisted session file: {e}")
        return dropped

    def get_running_summary(self) -> str:
        """Return the rolling summary of older exchanges (empty until first compaction)."""
        return self.rolling_summary


class ProjectMemory:
    """Manages project-specific memory"""
    
    def __init__(self, memory_store: MemoryStore):
        self.memory_store = memory_store
    
    def store_project(self, project_name: str, description: str, architecture: str = "") -> str:
        """Store project information"""
        content = f"Project: {project_name}\nDescription: {description}\nArchitecture: {architecture}"
        
        return self.memory_store.store_memory(
            content=content,
            memory_type="project",
            metadata={"project_name": project_name}
        )
    
    def get_project_context(self, project_name: str) -> str:
        """Get project context for development"""
        memories = self.memory_store.retrieve_memories(
            query=project_name,
            limit=5,
            memory_type="project"
        )
        
        if not memories:
            return f"No information found for project: {project_name}"
        
        context_parts = [f"Project: {project_name}"]
        for memory in memories:
            context_parts.append(memory["content"])
        
        return "\n\n".join(context_parts)


# Global instance
_memory_store = None


def get_memory_store() -> MemoryStore:
    """Get or create global memory store"""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store
