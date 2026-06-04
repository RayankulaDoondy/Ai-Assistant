"""Project intelligence — structured records for the things the user is working on.

A Project is the anchor entity Hunt uses to know which work the user is
currently focused on. Sessions can be linked to a project; the active
project's metadata is injected into every non-coder system prompt so Hunt
knows the stack, open tasks, and recent notes without being asked.

Trust model (per user decision): projects are ONLY user-created. Hunt never
auto-proposes a project from chat — the user creates them explicitly from
the Memory panel.

Backed by data/projects.json (atomic write, schema versioned). Mirrored to
MongoDB when Phase D sync is on. Local file remains source of truth.
"""
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Project field labels for the UI. Mirrors the PROFILE_FIELD_LABELS pattern.
PROJECT_FIELDS = ("name", "stack", "status", "description", "notes")
PROJECT_STATUSES = ("active", "paused", "done", "archived")
DEFAULT_STATUS = "active"


def _slugify(name: str) -> str:
    """Lowercase + dash-separated slug for natural-language project matching.

    Used by the `project_continue` intent so "switch to Travel Planner"
    resolves to the same record as "travel-planner".
    """
    if not name:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60]


class ProjectStore:
    """Atomic JSON-backed store for user projects + open tasks."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str = "./data/projects.json"):
        self.path = path
        # Index by project_id for O(1) lookup; serialized as a list for stable order.
        self._projects: Dict[str, Dict] = {}
        self._load()

    # ---- file I/O ----

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries = (data or {}).get("projects") or []
            if not isinstance(entries, list):
                return
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("id")
                if not pid:
                    continue
                # Ensure required fields exist (forward compat against schema bumps).
                entry.setdefault("name", "Untitled project")
                entry.setdefault("slug", _slugify(entry.get("name", "")))
                entry.setdefault("stack", "")
                entry.setdefault("status", DEFAULT_STATUS)
                entry.setdefault("description", "")
                entry.setdefault("notes", "")
                entry.setdefault("open_tasks", [])
                entry.setdefault("linked_session_ids", [])
                entry.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
                entry.setdefault("updated_at", entry["created_at"])
                self._projects[pid] = entry
            logger.info(f"Loaded {len(self._projects)} project(s) from {self.path}")
        except Exception as e:
            logger.warning(f"Could not load projects ({self.path}): {e}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            payload = {
                "schema": self.SCHEMA_VERSION,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "projects": list(self._projects.values()),
            }
            dir_ = os.path.dirname(self.path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(f"Could not save projects ({self.path}): {e}")
        # Phase D mirror — best-effort, no-op when Mongo isn't connected.
        self._mirror_to_mongo()

    def _mirror_to_mongo(self) -> None:
        try:
            from .mongo_sync import mongo_sync_singleton
            m = mongo_sync_singleton()
            if not m or not m.available:
                return
            # Upsert each project individually so partial syncs are useful.
            for pid, proj in self._projects.items():
                # Reuse the generic enqueue path: a dedicated collection per
                # entity type keeps the schema clean.
                m._enqueue(self._do_mongo_upsert, m, pid, proj)
        except Exception as e:
            logger.warning(f"Mongo project mirror failed (non-fatal): {e}")

    def _index_in_memory_store(self, proj: Dict) -> None:
        """Mirror this project into the vector + BM25 memory so retrieval
        across types ("Continue my travel project") finds it. Best-effort,
        non-fatal — if Chroma isn't initialised yet (early startup) we just
        log and move on; the next save will index again."""
        try:
            from . import memory_store as _ms
            store = _ms.get_memory_store()
            store.index_project(proj)
        except Exception as e:
            logger.debug(f"Project index skipped (non-fatal): {e}")

    def _drop_from_memory_store(self, project_id: str) -> None:
        try:
            from . import memory_store as _ms
            store = _ms.get_memory_store()
            store.remove_project_index(project_id)
        except Exception as e:
            logger.debug(f"Project de-index skipped (non-fatal): {e}")

    @staticmethod
    def _do_mongo_upsert(m, pid: str, proj: Dict) -> None:
        """Background-thread worker: actually writes the project doc."""
        doc = dict(proj)
        doc["device_id"] = m.device_id
        doc["updated_at_dt"] = datetime.utcnow()
        m._db.projects.update_one({"_id": pid}, {"$set": doc}, upsert=True)

    # ---- public API ----

    def list(self, *, include_archived: bool = False) -> List[Dict]:
        """Return projects newest-updated first; archived hidden by default."""
        items = list(self._projects.values())
        if not include_archived:
            items = [p for p in items if p.get("status") != "archived"]
        items.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return items

    def get(self, project_id: str) -> Optional[Dict]:
        return self._projects.get(project_id)

    def find_by_name_or_slug(self, query: str) -> Optional[Dict]:
        """Match user phrases like 'continue Travel Planner' to a record.

        Slugifies the query and looks for either an exact slug match or a
        substring match on the project name. Returns the most recently
        updated match when multiple hit.
        """
        if not query:
            return None
        q_slug = _slugify(query)
        q_lower = query.lower().strip()
        candidates = []
        for proj in self._projects.values():
            if proj.get("slug") == q_slug:
                return proj  # exact slug wins immediately
            name_lower = (proj.get("name") or "").lower()
            if q_lower and q_lower in name_lower:
                candidates.append(proj)
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return candidates[0]

    def create(self, name: str, **fields) -> Dict:
        """Create a new project. Returns the stored record."""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Project name is required")
        now = datetime.now().isoformat(timespec="seconds")
        pid = uuid.uuid4().hex
        proj = {
            "id": pid,
            "name": clean_name,
            "slug": _slugify(clean_name),
            "stack": (fields.get("stack") or "").strip(),
            "status": fields.get("status") or DEFAULT_STATUS,
            "description": (fields.get("description") or "").strip(),
            "notes": (fields.get("notes") or "").strip(),
            "open_tasks": [],
            "linked_session_ids": [],
            "created_at": now,
            "updated_at": now,
        }
        if proj["status"] not in PROJECT_STATUSES:
            proj["status"] = DEFAULT_STATUS
        self._projects[pid] = proj
        self._save()
        self._index_in_memory_store(proj)
        return proj

    def patch(self, project_id: str, **fields) -> Optional[Dict]:
        """Sparse update. Only fields present (non-None) are changed."""
        proj = self._projects.get(project_id)
        if not proj:
            return None
        changed = False
        for f in PROJECT_FIELDS:
            if f not in fields:
                continue
            v = fields[f]
            if v is None:
                continue
            cleaned = v.strip() if isinstance(v, str) else v
            if f == "status" and cleaned not in PROJECT_STATUSES:
                continue
            if f == "name" and not cleaned:
                continue
            if proj.get(f) != cleaned:
                proj[f] = cleaned
                if f == "name":
                    proj["slug"] = _slugify(cleaned)
                changed = True
        if changed:
            proj["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save()
            self._index_in_memory_store(proj)
        return proj

    def delete(self, project_id: str) -> bool:
        if project_id not in self._projects:
            return False
        del self._projects[project_id]
        self._save()
        self._drop_from_memory_store(project_id)
        return True

    # ---- tasks ----

    def add_task(self, project_id: str, text: str) -> Optional[Dict]:
        proj = self._projects.get(project_id)
        if not proj:
            return None
        text = (text or "").strip()
        if not text:
            return None
        task = {
            "id": uuid.uuid4().hex,
            "text": text,
            "done": False,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        proj["open_tasks"].append(task)
        proj["updated_at"] = task["created_at"]
        self._save()
        self._index_in_memory_store(proj)
        return task

    def patch_task(self, project_id: str, task_id: str, *,
                   text: Optional[str] = None,
                   done: Optional[bool] = None) -> Optional[Dict]:
        proj = self._projects.get(project_id)
        if not proj:
            return None
        for task in proj.get("open_tasks", []):
            if task.get("id") != task_id:
                continue
            if text is not None and isinstance(text, str):
                task["text"] = text.strip() or task["text"]
            if done is not None:
                task["done"] = bool(done)
            proj["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save()
            self._index_in_memory_store(proj)
            return task
        return None

    def remove_task(self, project_id: str, task_id: str) -> bool:
        proj = self._projects.get(project_id)
        if not proj:
            return False
        before = len(proj.get("open_tasks", []))
        proj["open_tasks"] = [t for t in proj["open_tasks"] if t.get("id") != task_id]
        if len(proj["open_tasks"]) == before:
            return False
        proj["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save()
        self._index_in_memory_store(proj)
        return True

    # ---- session links ----

    def link_session(self, project_id: str, session_id: str) -> bool:
        proj = self._projects.get(project_id)
        if not proj or not session_id:
            return False
        if session_id not in proj["linked_session_ids"]:
            proj["linked_session_ids"].append(session_id)
            proj["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save()
        return True

    # ---- context formatting ----

    def format_active_block(self, project_id: str) -> str:
        """Render a project as a system-prompt block.

        Returns "" if the project is missing. The block is added to the
        chat context whenever a project is active on the session.
        """
        proj = self._projects.get(project_id)
        if not proj:
            return ""
        lines = [f"Active project (the user's current focus):"]
        lines.append(f"- Name: {proj['name']}")
        if proj.get("stack"):
            lines.append(f"- Stack: {proj['stack']}")
        if proj.get("status") and proj["status"] != DEFAULT_STATUS:
            lines.append(f"- Status: {proj['status']}")
        if proj.get("description"):
            lines.append(f"- Description: {proj['description']}")
        open_tasks = [t for t in proj.get("open_tasks", []) if not t.get("done")]
        if open_tasks:
            lines.append("- Open tasks:")
            for t in open_tasks[:6]:
                lines.append(f"  • {t['text']}")
            if len(open_tasks) > 6:
                lines.append(f"  • …and {len(open_tasks) - 6} more")
        if proj.get("notes"):
            # First two non-empty lines of notes are usually enough context.
            note_lines = [l for l in proj["notes"].split("\n") if l.strip()][:2]
            for nl in note_lines:
                lines.append(f"- Note: {nl[:200]}")
        return "\n".join(lines)


# ----------------------------------------------------------- singleton

_project_store: Optional[ProjectStore] = None


def get_project_store(path: Optional[str] = None) -> ProjectStore:
    global _project_store
    if _project_store is None:
        _project_store = ProjectStore(path or "./data/projects.json")
    return _project_store
