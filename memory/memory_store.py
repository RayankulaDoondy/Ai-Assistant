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

            # Advanced RAG state — all lazy, loaded on first use so a cold
            # start doesn't pay the model-download cost. Cleared on writes
            # so stale BM25 indexes don't shadow new content.
            self._reranker = None                  # CrossEncoder, lazy
            self._reranker_load_failed = False     # don't retry every query
            self._bm25_cache: Dict[tuple, Dict] = {}  # types-tuple -> {"index", "docs", "ids", "metas"}

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
            # Any cached BM25 index is now stale — next keyword query rebuilds.
            self._invalidate_bm25_cache()

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
    
    # ================================================================ #
    # Advanced RAG — reranker, BM25 hybrid, and multi-source retrieval
    # ================================================================ #

    def _load_reranker(self):
        """Lazy-load the cross-encoder reranker. Returns the model or None
        when unavailable (model download failed, sentence_transformers missing,
        config disabled). Failure is sticky for the process so we don't retry
        the import on every query."""
        if self._reranker is not None:
            return self._reranker
        if self._reranker_load_failed:
            return None
        try:
            from config.settings import settings as _settings
            if not getattr(_settings, "MEMORY_RERANK_ENABLED", False):
                self._reranker_load_failed = True
                return None
            from sentence_transformers import CrossEncoder
            model_name = getattr(_settings, "MEMORY_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
            logger.info(f"Loading cross-encoder reranker: {model_name}")
            self._reranker = CrossEncoder(model_name)
            return self._reranker
        except Exception as e:
            logger.warning(f"Reranker unavailable ({e}); falling back to raw cosine ranking")
            self._reranker_load_failed = True
            return None

    def rerank_candidates(self, query: str, candidates: List[Dict], top_k: int) -> List[Dict]:
        """Score (query, candidate.content) pairs with the cross-encoder, return top_k.

        Returns the input list ordered by relevance with a new "rerank_score"
        field. If reranking is unavailable, returns the candidates trimmed to
        top_k without reordering.
        """
        if not candidates:
            return []
        model = self._load_reranker()
        if model is None:
            return candidates[:top_k]
        try:
            pairs = [(query, (c.get("content") or "")[:2000]) for c in candidates]
            scores = model.predict(pairs)
            for c, s in zip(candidates, scores):
                c["rerank_score"] = float(s)
            candidates.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
            return candidates[:top_k]
        except Exception as e:
            logger.warning(f"Rerank scoring failed ({e}); using raw cosine order")
            return candidates[:top_k]

    # ---------------------------------------------------------------- #
    # BM25 keyword search — complements cosine similarity for exact
    # terms (URLs, IDs, file paths, brand names) that semantics misses.
    # ---------------------------------------------------------------- #

    def _invalidate_bm25_cache(self) -> None:
        """Drop the BM25 index. Called after every write so the next search rebuilds."""
        if self._bm25_cache:
            self._bm25_cache.clear()

    @staticmethod
    def _bm25_tokenize(text: str) -> List[str]:
        """Cheap tokenizer for BM25: lowercase, split on non-alphanumeric. Good
        enough for chat/notes content; we don't need stemming here."""
        import re as _re
        return [t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if t]

    def _build_bm25_for_types(self, types: tuple) -> Dict:
        """Build (or fetch cached) BM25 index over all docs whose memory_type
        is in `types`. Cached per types tuple so repeated searches are O(1)."""
        if types in self._bm25_cache:
            return self._bm25_cache[types]
        try:
            from rank_bm25 import BM25Okapi
        except Exception as e:
            logger.info(f"rank_bm25 unavailable ({e}); BM25 disabled")
            self._bm25_cache[types] = {"index": None, "docs": [], "ids": [], "metas": []}
            return self._bm25_cache[types]

        if types and len(types) == 1:
            where = {"type": types[0]}
        elif types:
            where = {"type": {"$in": list(types)}}
        else:
            where = None

        try:
            raw = self.collection.get(where=where)
        except Exception as e:
            logger.warning(f"BM25 fetch failed ({e}); skipping keyword search")
            self._bm25_cache[types] = {"index": None, "docs": [], "ids": [], "metas": []}
            return self._bm25_cache[types]

        docs = raw.get("documents") or []
        ids = raw.get("ids") or []
        metas = raw.get("metadatas") or []
        if not docs:
            self._bm25_cache[types] = {"index": None, "docs": [], "ids": [], "metas": []}
            return self._bm25_cache[types]

        tokenized = [self._bm25_tokenize(d) for d in docs]
        index = BM25Okapi(tokenized)
        entry = {"index": index, "docs": docs, "ids": ids, "metas": metas}
        self._bm25_cache[types] = entry
        logger.debug(f"BM25 index built for types={types}: {len(docs)} docs")
        return entry

    def keyword_search(self, query: str, limit: int, types: Optional[List[str]] = None) -> List[Dict]:
        """BM25 keyword search. Returns at most `limit` candidates in score order."""
        if not query or not query.strip():
            return []
        types_tuple = tuple(sorted(types)) if types else tuple()
        entry = self._build_bm25_for_types(types_tuple)
        index = entry["index"]
        if index is None or not entry["docs"]:
            return []
        tokens = self._bm25_tokenize(query)
        if not tokens:
            return []
        try:
            scores = index.get_scores(tokens)
        except Exception as e:
            logger.warning(f"BM25 scoring failed ({e}); returning empty keyword hits")
            return []

        # Pull the top `limit` by score (descending).
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:limit]
        out = []
        for rank, i in enumerate(ranked):
            if scores[i] <= 0:
                break
            out.append({
                "content": entry["docs"][i],
                "id": entry["ids"][i] if i < len(entry["ids"]) else "",
                "metadata": entry["metas"][i] if i < len(entry["metas"]) else {},
                "bm25_score": float(scores[i]),
                "bm25_rank": rank,
            })
        return out

    # ---------------------------------------------------------------- #
    # Hybrid search — vector + BM25 merged with Reciprocal Rank Fusion.
    # Multi-source — searches across multiple memory_type values at once.
    # ---------------------------------------------------------------- #

    def vector_search(self, query: str, limit: int, types: Optional[List[str]] = None) -> List[Dict]:
        """Cosine similarity search across the given types. Returns ranked candidates."""
        if not query or not query.strip():
            return []
        try:
            if types and len(types) == 1:
                where = {"type": types[0]}
            elif types:
                where = {"type": {"$in": list(types)}}
            else:
                where = None
            results = self.collection.query(query_texts=[query], n_results=limit, where=where)
        except Exception as e:
            logger.warning(f"Vector search failed ({e}); returning empty")
            return []
        out: List[Dict] = []
        if not (results and results.get("documents")):
            return out
        docs = results["documents"][0]
        ids = (results.get("ids") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            out.append({
                "content": doc,
                "id": ids[i] if i < len(ids) else "",
                "metadata": metas[i] if i < len(metas) else {},
                "distance": float(dists[i]) if i < len(dists) else 0.0,
                "vector_rank": i,
            })
        return out

    def hybrid_search(self, query: str, limit: int, types: Optional[List[str]] = None) -> List[Dict]:
        """Vector + BM25 merged via Reciprocal Rank Fusion.

        RRF score for an item = sum over each source of 1/(K + rank_in_source).
        K (default 60) softens the contribution of low-ranked hits. An item
        ranked #1 in both sources beats an item ranked #1 in only one.
        """
        try:
            from config.settings import settings as _settings
            hybrid_on = getattr(_settings, "MEMORY_HYBRID_ENABLED", True)
            rrf_k = int(getattr(_settings, "MEMORY_HYBRID_RRF_K", 60) or 60)
        except Exception:
            hybrid_on, rrf_k = True, 60

        # Pull more than `limit` from each source so the merge has material to
        # work with. Reranker (if enabled) trims back to `limit`.
        per_source = max(limit * 3, 10)
        vec = self.vector_search(query, per_source, types=types)
        if not hybrid_on:
            return vec[:limit]

        kw = self.keyword_search(query, per_source, types=types)
        if not kw:
            return vec[:limit]

        # RRF merge — dedup by id, sum reciprocal ranks.
        scored: Dict[str, Dict] = {}
        for hit in vec:
            key = hit["id"] or hit["content"][:80]
            scored.setdefault(key, dict(hit))
            scored[key]["rrf_score"] = scored[key].get("rrf_score", 0.0) + 1.0 / (rrf_k + hit["vector_rank"] + 1)
        for hit in kw:
            key = hit["id"] or hit["content"][:80]
            if key in scored:
                scored[key]["bm25_score"] = hit["bm25_score"]
                scored[key]["bm25_rank"] = hit["bm25_rank"]
            else:
                scored[key] = dict(hit)
            scored[key]["rrf_score"] = scored[key].get("rrf_score", 0.0) + 1.0 / (rrf_k + hit["bm25_rank"] + 1)

        merged = sorted(scored.values(), key=lambda c: c.get("rrf_score", 0.0), reverse=True)
        return merged[:max(limit * 2, limit)]  # keep extras for the reranker

    def retrieve(
        self,
        query: str,
        limit: int = 5,
        types: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Advanced retrieval: hybrid search → rerank → top K.

        This is the entry point higher layers (chat context builder, /memory
        endpoints) should use going forward. `retrieve_memories` stays for
        back-compat with anyone calling it directly.

        - `types` filters by memory_type. None = all types.
        - With reranker on: fetches ~K*4 candidates, rescues the top K by
          cross-encoder relevance.
        - With reranker off: returns the top K from hybrid search directly.
        """
        if not query or not query.strip():
            return []
        try:
            from config.settings import settings as _settings
            rerank_on = getattr(_settings, "MEMORY_RERANK_ENABLED", True)
            fetch_mult = int(getattr(_settings, "MEMORY_RERANK_FETCH_MULTIPLIER", 4) or 4)
        except Exception:
            rerank_on, fetch_mult = True, 4

        fetch_k = max(limit * fetch_mult, limit) if rerank_on else limit
        candidates = self.hybrid_search(query, fetch_k, types=types)
        if not candidates:
            return []
        if rerank_on:
            return self.rerank_candidates(query, candidates, limit)
        return candidates[:limit]

    # ---------------------------------------------------------------- #
    # Multi-source indexing helpers — projects + tasks get embedded too.
    # ---------------------------------------------------------------- #

    def _delete_by_metadata(self, metadata_filter: Dict) -> int:
        """Delete all stored memories matching a metadata filter. Returns count."""
        try:
            existing = self.collection.get(where=metadata_filter)
            ids = existing.get("ids") or []
            if not ids:
                return 0
            self.collection.delete(ids=ids)
            self._invalidate_bm25_cache()
            return len(ids)
        except Exception as e:
            logger.warning(f"Delete-by-metadata failed ({e}); skipping")
            return 0

    def index_project(self, project: Dict) -> Optional[str]:
        """Embed a project record so it surfaces in semantic + keyword search.
        Re-indexes (deletes old + writes new) so updates stay consistent."""
        pid = project.get("id")
        if not pid:
            return None
        # Drop any prior chunks for this project.
        self._delete_by_metadata({"project_id": pid})

        bits: List[str] = []
        bits.append(f"Project: {project.get('name', '')}")
        if project.get("stack"):
            bits.append(f"Stack: {project['stack']}")
        if project.get("status"):
            bits.append(f"Status: {project['status']}")
        if project.get("description"):
            bits.append(f"Description: {project['description']}")
        if project.get("notes"):
            bits.append(f"Notes: {project['notes']}")
        content = "\n".join(b for b in bits if b)
        if not content.strip():
            return None
        mid = self.store_memory(
            content=content,
            memory_type="project",
            metadata={"project_id": pid, "project_name": project.get("name", "")},
        )
        # Index each open task as its own searchable chunk so "what tasks did
        # we plan for X" can hit individual task text, not just the project blob.
        for task in (project.get("open_tasks") or []):
            t_text = (task.get("text") or "").strip()
            if not t_text:
                continue
            self.store_memory(
                content=f"Task on {project.get('name','project')}: {t_text}",
                memory_type="task",
                metadata={
                    "project_id": pid,
                    "task_id": task.get("id"),
                    "done": bool(task.get("done")),
                },
            )
        return mid

    def remove_project_index(self, project_id: str) -> int:
        """Drop all indexed content tied to this project (project blob + tasks)."""
        n = self._delete_by_metadata({"project_id": project_id})
        return n

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
            self._invalidate_bm25_cache()
            logger.info(f"Updated memory: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating memory: {str(e)}")
            return False

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory item"""
        try:
            self.collection.delete(ids=[memory_id])
            self._invalidate_bm25_cache()
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
        """Get relevant long-term conversation context via the advanced
        retrieve pipeline (hybrid vector+BM25 search → cross-encoder rerank).

        Conversation-only — for multi-source context that also taps projects /
        tasks / documents, see `get_multi_source_context`.
        """
        relevant_memories = self.memory_store.retrieve(
            query=query,
            limit=limit,
            types=["conversation"],
        )

        if not relevant_memories:
            return ""

        context_parts = ["Relevant previous conversations:"]
        for memory in relevant_memories:
            context_parts.append(f"- {memory['content'][:200]}...")

        return "\n".join(context_parts)

    def get_multi_source_context(self, query: str, limit: int = 5) -> str:
        """Search ALL configured memory types (conversation, project, task,
        document) and format the merged hits as a single context block.

        Each hit is labelled by source type so the LLM can attribute facts
        ("from a stored document" vs "from an earlier chat") without us
        having to wire structured tool calls.
        """
        try:
            from config.settings import settings as _settings
            raw = (getattr(_settings, "MEMORY_SEARCH_TYPES", "conversation") or "").strip()
            types = [t.strip() for t in raw.split(",") if t.strip()]
        except Exception:
            types = ["conversation", "project", "task", "document"]

        hits = self.memory_store.retrieve(query=query, limit=limit, types=types or None)
        if not hits:
            return ""

        # Group by type so the prompt block reads top-down by source.
        by_type: Dict[str, List[Dict]] = {}
        for h in hits:
            t = ((h.get("metadata") or {}).get("type") or "memory")
            by_type.setdefault(t, []).append(h)

        order = ["project", "task", "document", "conversation"]
        out_lines: List[str] = ["Relevant context from your memory:"]
        for t in order:
            items = by_type.get(t, [])
            if not items:
                continue
            label = {
                "project": "Projects",
                "task": "Open tasks",
                "document": "Documents",
                "conversation": "Past chat",
            }.get(t, t.title())
            out_lines.append(f"[{label}]")
            for h in items:
                snippet = (h.get("content") or "")[:240].replace("\n", " ")
                out_lines.append(f"- {snippet}")
        return "\n".join(out_lines)

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
