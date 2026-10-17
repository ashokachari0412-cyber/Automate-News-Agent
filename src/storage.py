"""Shared on-disk persistence: news cache and Alfred conversation memory."""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.models.news import PrioritizedNewsItem

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
NEWS_CACHE_FILE = ARTIFACTS_DIR / "news_cache.json"
CHAT_MEMORY_FILE = ARTIFACTS_DIR / "chat_memory.json"
CHAT_HISTORY_FILE = ARTIFACTS_DIR / "chat_history.json"
MAX_SESSIONS = 15


def load_news() -> Dict[str, PrioritizedNewsItem]:
    if not NEWS_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(NEWS_CACHE_FILE.read_text(encoding="utf-8"))
        return {k: PrioritizedNewsItem(**v) for k, v in data.items()}
    except Exception as e:
        logger.warning(f"Failed to load news cache: {e}")
        return {}


def save_news(items: Dict[str, PrioritizedNewsItem]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: v.model_dump(mode="json") for k, v in items.items()}
    NEWS_CACHE_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def merge_news(items: List[PrioritizedNewsItem]) -> None:
    cache = load_news()
    for it in items:
        cache[it.id] = it
    save_news(cache)


def load_memory() -> dict:
    """Memory layout: {"messages": [{role, content, ...extras}], "last_news_id": str|None}."""
    if CHAT_MEMORY_FILE.exists():
        try:
            return json.loads(CHAT_MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load chat memory: {e}")
    return {"messages": [], "last_news_id": None}


def save_memory(memory: dict, max_messages: int = 200) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    memory["messages"] = memory.get("messages", [])[-max_messages:]
    memory.setdefault("session_id", uuid.uuid4().hex[:12])
    memory["updated_at"] = datetime.now().isoformat(timespec="seconds")
    CHAT_MEMORY_FILE.write_text(json.dumps(memory, indent=2, default=str), encoding="utf-8")


# ---- Chat history: past sessions (newest first), capped at MAX_SESSIONS ----

def _load_sessions() -> List[dict]:
    if CHAT_HISTORY_FILE.exists():
        try:
            return json.loads(CHAT_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load chat history: {e}")
    return []


def _save_sessions(sessions: List[dict]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_HISTORY_FILE.write_text(json.dumps(sessions[:MAX_SESSIONS], indent=2, default=str), encoding="utf-8")


def _session_title(messages: List[dict]) -> str:
    first = next((m.get("content", "") for m in messages if m.get("role") == "user"), "") or "Untitled chat"
    first = " ".join(first.split())
    return first[:60] + ("..." if len(first) > 60 else "")


def archive_current_session() -> None:
    """Moves the active conversation into history (if it has messages) and starts a fresh one."""
    memory = load_memory()
    messages = memory.get("messages", [])
    if messages:
        sid = memory.get("session_id") or uuid.uuid4().hex[:12]
        sessions = [s for s in _load_sessions() if s.get("id") != sid]
        sessions.insert(0, {
            "id": sid,
            "title": _session_title(messages),
            "updated_at": memory.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
            "messages": messages,
            "last_news_id": memory.get("last_news_id"),
        })
        _save_sessions(sessions)
    save_memory({"messages": [], "last_news_id": None, "session_id": uuid.uuid4().hex[:12]})


def list_sessions() -> List[dict]:
    return [{"id": s["id"], "title": s.get("title", "Untitled chat"), "updated_at": s.get("updated_at"),
             "count": len(s.get("messages", []))} for s in _load_sessions()]


def open_session(session_id: str) -> Optional[dict]:
    """Makes a past session the active conversation (the current one is archived first)."""
    target = next((s for s in _load_sessions() if s.get("id") == session_id), None)
    if not target:
        return None
    archive_current_session()
    _save_sessions([s for s in _load_sessions() if s.get("id") != session_id])
    memory = {"messages": target.get("messages", []), "last_news_id": target.get("last_news_id"),
              "session_id": session_id}
    save_memory(memory)
    return memory
