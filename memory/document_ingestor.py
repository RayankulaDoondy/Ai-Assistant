"""Document ingestion for Hunt's RAG system.

Reads a file (txt / md / source code / pdf), splits it into overlapping
character-windowed chunks, and feeds each chunk into the existing
ChromaDB collection as `memory_type="document"`. Once indexed, the
same advanced-retrieve pipeline (vector + BM25 hybrid + cross-encoder
reranker) surfaces the chunks alongside chat history.

Public API:
    ingest_file(path, title=None, doc_id=None) -> dict
    delete_document(doc_id) -> int
    list_documents() -> list[dict]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# File extensions we know how to handle. Anything else falls into the
# generic text-read path; binary files outside this set are rejected.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".html", ".css", ".scss", ".sass",
    ".json", ".yaml", ".yml", ".toml", ".xml",
    ".sh", ".bash", ".ps1", ".bat",
    ".sql", ".env", ".gitignore", ".dockerfile",
}
PDF_EXTENSIONS = {".pdf"}


# --------------------------------------------------------------------- #
# Manifest — small JSON index of ingested documents so the UI can list /
# delete them without scanning every chunk in Chroma. Source of truth for
# "what documents exist" — the chunks themselves live only in Chroma.
# --------------------------------------------------------------------- #

def _store_dir() -> str:
    from config.settings import settings
    path = getattr(settings, "DOCUMENT_STORE_DIR", "./data/documents")
    os.makedirs(path, exist_ok=True)
    return path


def _manifest_path() -> str:
    return os.path.join(_store_dir(), "manifest.json")


def _load_manifest() -> Dict[str, Dict]:
    p = _manifest_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        return data.get("documents") or {}
    except Exception as e:
        logger.warning(f"Document manifest unreadable ({e}); starting empty")
        return {}


def _save_manifest(documents: Dict[str, Dict]) -> None:
    p = _manifest_path()
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        payload = {
            "schema": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "documents": documents,
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(p) or ".",
            delete=False, suffix=".tmp",
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, p)
    except Exception as e:
        logger.warning(f"Could not save document manifest: {e}")


# --------------------------------------------------------------------- #
# Extractors — convert one file to a single text blob. Each returns ""
# rather than raising so a malformed file fails soft.
# --------------------------------------------------------------------- #

def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception as e:
        logger.warning(f"Failed to read text file {path}: {e}")
        return ""


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except Exception as e:
        logger.warning(f"pypdf not installed; can't read {path} ({e})")
        return ""
    try:
        reader = PdfReader(path)
        out: List[str] = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                out.append(f"[Page {i + 1}]\n{text}")
        return "\n\n".join(out)
    except Exception as e:
        logger.warning(f"PDF parse failed for {path}: {e}")
        return ""


def _extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXTENSIONS:
        return _read_pdf(path)
    if ext in TEXT_EXTENSIONS or ext == "":
        return _read_text_file(path)
    # Unknown extension — try as text but warn.
    logger.info(f"Unknown extension '{ext}' for {path}; reading as text")
    return _read_text_file(path)


# --------------------------------------------------------------------- #
# Chunker — sliding character window with overlap. Tries to break at
# paragraph boundaries when one is near the cut point so chunks land on
# coherent units instead of mid-sentence.
# --------------------------------------------------------------------- #

def _chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> List[str]:
    """Split text into overlapping ~chunk_chars windows. Snaps to nearest
    paragraph break within ±15% of the target size when possible.
    """
    text = (text or "").strip()
    if not text:
        return []
    n = len(text)
    if n <= chunk_chars:
        return [text]

    chunks: List[str] = []
    start = 0
    snap_window = max(int(chunk_chars * 0.15), 80)

    while start < n:
        end = min(start + chunk_chars, n)
        if end < n:
            # Look for a paragraph or sentence boundary near `end`.
            slice_start = max(end - snap_window, start + 1)
            window = text[slice_start:end + snap_window]
            # Prefer double-newline (paragraph), then single newline,
            # then sentence-ending punctuation. Find the latest such
            # break in the window so we extend forward rather than truncate.
            best = -1
            for marker in ("\n\n", "\n", ". ", "! ", "? "):
                idx = window.rfind(marker)
                if idx > best:
                    best = idx + len(marker)
            if best > 0:
                end = slice_start + best
                end = min(end, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        # Advance start with overlap; never go backwards.
        start = max(end - overlap_chars, start + 1)
    return chunks


# --------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------- #

def ingest_file(
    path: str,
    title: Optional[str] = None,
    doc_id: Optional[str] = None,
) -> Dict:
    """Read, chunk, and index a single file.

    Returns a dict:
        {
          "doc_id": str, "title": str, "source": path, "chunks": int,
          "bytes": int, "error": str|None
        }
    A non-None `error` indicates the file was rejected and no chunks were
    written. Documents above DOCUMENT_MAX_BYTES are rejected up front to
    keep ingestion predictable.
    """
    from config.settings import settings
    from . import memory_store as _ms

    if not path or not os.path.exists(path):
        return {"doc_id": "", "title": title or "", "source": path,
                "chunks": 0, "bytes": 0, "error": "file not found"}

    size = os.path.getsize(path)
    max_bytes = getattr(settings, "DOCUMENT_MAX_BYTES", 10 * 1024 * 1024)
    if size > max_bytes:
        return {"doc_id": "", "title": title or os.path.basename(path),
                "source": path, "chunks": 0, "bytes": size,
                "error": f"file exceeds DOCUMENT_MAX_BYTES ({max_bytes})"}

    text = _extract_text(path)
    if not text.strip():
        return {"doc_id": "", "title": title or os.path.basename(path),
                "source": path, "chunks": 0, "bytes": size,
                "error": "no extractable text"}

    chunk_chars = int(getattr(settings, "DOCUMENT_CHUNK_CHARS", 1200) or 1200)
    overlap = int(getattr(settings, "DOCUMENT_CHUNK_OVERLAP", 150) or 150)
    chunks = _chunk_text(text, chunk_chars, overlap)
    if not chunks:
        return {"doc_id": "", "title": title or os.path.basename(path),
                "source": path, "chunks": 0, "bytes": size,
                "error": "chunker returned empty"}

    final_doc_id = doc_id or uuid.uuid4().hex
    final_title = (title or os.path.basename(path)).strip()
    sha = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]

    store = _ms.get_memory_store()
    # If this doc_id was previously indexed, drop old chunks so re-ingest
    # doesn't accumulate duplicates.
    try:
        store._delete_by_metadata({"doc_id": final_doc_id})
    except Exception:
        pass

    for i, chunk in enumerate(chunks):
        store.store_memory(
            content=chunk,
            memory_type="document",
            metadata={
                "doc_id": final_doc_id,
                "doc_title": final_title,
                "chunk_index": i,
                "chunk_total": len(chunks),
                "source": path,
                "sha": sha,
            },
        )

    # Update manifest so the UI can list / delete docs without scanning Chroma.
    manifest = _load_manifest()
    manifest[final_doc_id] = {
        "doc_id": final_doc_id,
        "title": final_title,
        "source": path,
        "bytes": size,
        "chunks": len(chunks),
        "sha": sha,
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_manifest(manifest)

    logger.info(f"Indexed document '{final_title}' as {final_doc_id} "
                f"({len(chunks)} chunks, {size} bytes)")
    return {
        "doc_id": final_doc_id,
        "title": final_title,
        "source": path,
        "chunks": len(chunks),
        "bytes": size,
        "error": None,
    }


def delete_document(doc_id: str) -> int:
    """Drop all chunks for a document and its manifest entry. Returns the
    number of chunks removed (0 if doc_id is unknown)."""
    if not doc_id:
        return 0
    from . import memory_store as _ms
    store = _ms.get_memory_store()
    n = store._delete_by_metadata({"doc_id": doc_id})
    manifest = _load_manifest()
    if doc_id in manifest:
        del manifest[doc_id]
        _save_manifest(manifest)
    logger.info(f"Removed document {doc_id} ({n} chunks)")
    return n


def list_documents() -> List[Dict]:
    """Return the manifest as a sorted list (newest first by indexed_at)."""
    manifest = _load_manifest()
    items = list(manifest.values())
    items.sort(key=lambda d: d.get("indexed_at", ""), reverse=True)
    return items


def get_document(doc_id: str) -> Optional[Dict]:
    manifest = _load_manifest()
    return manifest.get(doc_id)
