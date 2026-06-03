"""MongoDB Atlas cloud sync — best-effort backup of session + profile + facts + actions.

Design rules:
  - Local files remain source of truth. Mongo is a write-through cache.
  - Writes happen on a background worker thread so they never block /chat.
  - Connection errors and dead network are non-fatal — they get logged and
    the queue drains when Mongo returns.
  - pymongo is imported lazily so Hunt runs without it installed (Mongo
    sync just stays disabled).

Collections (all keyed off `device_id` so multi-device sync is possible later):
  - sessions(_id, title, started_at, updated_at, device_id, verbatim[], rolling_summary)
  - profile(_id=device_id, name, occupation, ..., updated_at)
  - facts(_id, text, pattern, source_session, device_id, saved_at)
  - actions(_id, action, params, decision, status, detail, device_id, timestamp_dt)
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MongoSync:
    """Thread-safe queue-backed writer to MongoDB Atlas."""

    QUEUE_LIMIT = 1000
    CONNECT_TIMEOUT_MS = 3000

    def __init__(self, uri: str, db_name: str = "hunt", device_id: str = "default"):
        self.uri = uri
        self.db_name = db_name
        self.device_id = device_id
        self._client = None
        self._db = None
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=self.QUEUE_LIMIT)
        self._worker: Optional[threading.Thread] = None
        self.last_ok_at: Optional[str] = None
        self.last_err: Optional[str] = None
        self.writes_attempted = 0
        self.writes_failed = 0
        self._connect()
        self._start_worker()

    # ------------------------------------------------------------------ wire-up

    def _connect(self) -> None:
        if not self.uri:
            return
        try:
            from pymongo import MongoClient  # local import; optional dep
        except ImportError:
            logger.warning(
                "pymongo not installed — Mongo sync disabled. "
                'Run: ./.venv/Scripts/pip install "pymongo[srv]"'
            )
            return
        try:
            client = MongoClient(self.uri, serverSelectionTimeoutMS=self.CONNECT_TIMEOUT_MS)
            client.admin.command("ping")
            self._client = client
            self._db = client[self.db_name]
            # Light indexes for the queries we'll actually make.
            self._db.sessions.create_index([("updated_at", -1)])
            self._db.sessions.create_index([("device_id", 1), ("updated_at", -1)])
            self._db.actions.create_index([("timestamp_dt", -1)])
            self._db.facts.create_index([("saved_at", -1)])
            logger.info(f"MongoDB connected: db='{self.db_name}', device='{self.device_id}'")
        except Exception as e:
            self.last_err = str(e)
            logger.warning(f"MongoDB connection failed ({e}); running local-only")
            self._client = None
            self._db = None

    def _start_worker(self) -> None:
        if self._worker is not None:
            return
        t = threading.Thread(target=self._run_worker, daemon=True, name="mongo-sync")
        t.start()
        self._worker = t

    def _run_worker(self) -> None:
        # Drains the queue forever. Crashes are swallowed and logged so a
        # single bad item never kills the loop.
        while True:
            try:
                item = self._queue.get()
                if item is None:
                    break
                fn, args, kwargs = item
                self.writes_attempted += 1
                try:
                    fn(*args, **kwargs)
                    self.last_ok_at = datetime.now().isoformat(timespec="seconds")
                except Exception as e:
                    self.writes_failed += 1
                    self.last_err = str(e)
                    logger.warning(f"Mongo write failed ({fn.__name__}): {e}")
                self._queue.task_done()
            except Exception as e:
                logger.error(f"mongo worker error: {e}")

    def _enqueue(self, fn: Callable, *args, **kwargs) -> None:
        if not self.available:
            return
        try:
            self._queue.put_nowait((fn, args, kwargs))
        except queue.Full:
            logger.warning("Mongo sync queue full — dropping write")

    # ------------------------------------------------------------------ public

    @property
    def available(self) -> bool:
        return self._db is not None

    def status(self) -> Dict[str, Any]:
        return {
            "configured": bool(self.uri),
            "connected": self.available,
            "device_id": self.device_id,
            "db_name": self.db_name,
            "queue_depth": self._queue.qsize(),
            "writes_attempted": self.writes_attempted,
            "writes_failed": self.writes_failed,
            "last_ok_at": self.last_ok_at,
            "last_err": self.last_err,
        }

    # ---- writes ----

    def upsert_session(self, session_id: str, payload: Dict[str, Any]) -> None:
        self._enqueue(self._do_upsert_session, session_id, payload)

    def _do_upsert_session(self, session_id: str, payload: Dict[str, Any]) -> None:
        doc = dict(payload)
        doc["device_id"] = self.device_id
        doc["updated_at"] = datetime.utcnow()
        self._db.sessions.update_one({"_id": session_id}, {"$set": doc}, upsert=True)

    def upsert_profile(self, profile_data: Dict[str, Any]) -> None:
        self._enqueue(self._do_upsert_profile, profile_data)

    def _do_upsert_profile(self, profile_data: Dict[str, Any]) -> None:
        doc = dict(profile_data)
        doc["device_id"] = self.device_id
        doc["updated_at"] = datetime.utcnow()
        # Keyed by device id so multiple machines can later coexist.
        self._db.profile.update_one({"_id": self.device_id}, {"$set": doc}, upsert=True)

    def add_action(self, entry: Dict[str, Any]) -> None:
        self._enqueue(self._do_add_action, entry)

    def _do_add_action(self, entry: Dict[str, Any]) -> None:
        doc = dict(entry)
        doc["device_id"] = self.device_id
        doc["timestamp_dt"] = datetime.utcnow()
        self._db.actions.insert_one(doc)

    def add_fact(self, text: str, pattern: Optional[str] = None,
                 source_session: Optional[str] = None) -> None:
        self._enqueue(self._do_add_fact, text, pattern, source_session)

    def _do_add_fact(self, text: str, pattern: Optional[str],
                     source_session: Optional[str]) -> None:
        doc = {
            "text": text,
            "pattern": pattern,
            "source_session": source_session,
            "device_id": self.device_id,
            "saved_at": datetime.utcnow(),
        }
        self._db.facts.insert_one(doc)

    # ---- reads (synchronous; used by /sessions endpoints) ----

    def list_sessions(self, limit: int = 30) -> List[Dict[str, Any]]:
        if not self.available:
            return []
        try:
            cursor = self._db.sessions.find(
                {},
                {
                    "_id": 1,
                    "title": 1,
                    "started_at": 1,
                    "updated_at": 1,
                    "device_id": 1,
                    # Just the count; the client doesn't need every turn for the list.
                    "verbatim": {"$slice": 0},
                }
            ).sort("updated_at", -1).limit(limit)
            return [self._serialize(d) for d in cursor]
        except Exception as e:
            logger.warning(f"list_sessions failed: {e}")
            return []

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        try:
            doc = self._db.sessions.find_one({"_id": session_id})
            return self._serialize(doc) if doc else None
        except Exception as e:
            logger.warning(f"get_session failed: {e}")
            return None

    # ---- helpers ----

    @staticmethod
    def _serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        out = dict(doc)
        for k, v in list(out.items()):
            if isinstance(v, datetime):
                out[k] = v.isoformat()
        return out


# ---------------------------------------------------------- module singleton

_mongo: Optional[MongoSync] = None


def get_mongo_sync(uri: Optional[str] = None,
                   db_name: Optional[str] = None,
                   device_id: Optional[str] = None) -> Optional[MongoSync]:
    """Get or initialize the singleton. Returns None when uri is empty."""
    global _mongo
    if _mongo is None and uri:
        _mongo = MongoSync(uri=uri, db_name=db_name or "hunt", device_id=device_id or "default")
    return _mongo


def mongo_sync_singleton() -> Optional[MongoSync]:
    """Loose-coupled accessor used by other memory modules. Returns None
    when sync isn't configured — callers just skip the write silently."""
    return _mongo
