"""Shared on-disk persistence: news cache and Alfred conversation memory."""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

from src.models.news import PrioritizedNewsItem

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
NEWS_CACHE_FILE = ARTIFACTS_DIR / "news_cache.json"
CHAT_MEMORY_FILE = ARTIFACTS_DIR / "chat_memory.json"
CHAT_HISTORY_FILE = ARTIFACTS_DIR / "chat_history.json"
CAPTIONS_FILE = ARTIFACTS_DIR / "captions.json"
DEFINITIONS_FILE = ARTIFACTS_DIR / "definitions.json"
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


SEEN_DAYS = 14       # a story shown to the user in the last 14 days is not shown again
KEEP_DAYS = 14       # older stories are dropped from the cache (>= SEEN_DAYS, or shown ones are forgotten)
SIMILAR_TITLE = 0.72  # headline similarity that counts as the same story


def _norm_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def _story_key(it: PrioritizedNewsItem) -> str:
    """Stable id: same source link / headline -> same id on every scan (positional ids collided across days)."""
    basis = sorted(it.source_urls)[0] if it.source_urls else _norm_title(it.title)
    return "n_" + hashlib.sha1(basis.encode()).hexdigest()[:10]


def _same_story(a: PrioritizedNewsItem, b: PrioritizedNewsItem) -> bool:
    if set(a.source_urls) & set(b.source_urls):
        return True
    ta, tb = _norm_title(a.title), _norm_title(b.title)
    return SequenceMatcher(None, ta, tb).ratio() >= SIMILAR_TITLE or (len(ta) > 15 and (ta in tb or tb in ta))


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def apply_scan(cache: Dict[str, PrioritizedNewsItem], items: List[PrioritizedNewsItem]) -> List[PrioritizedNewsItem]:
    """Add a fresh scan to the cache (in place) and return only the genuinely new stories, best first.

    - duplicates inside the scan are merged (highest score wins)
    - stories already SHOWN to the user in the last SEEN_DAYS are skipped
      (scheduled background scans don't count as "seen")
    - stories older than KEEP_DAYS are pruned
    """
    now = datetime.now(timezone.utc)
    for old_id in [k for k, v in cache.items() if (_as_utc(v.scanned_at) or datetime.min.replace(tzinfo=timezone.utc)) < now - timedelta(days=KEEP_DAYS)]:
        del cache[old_id]
    recent = [v for v in cache.values() if _as_utc(v.shown_at) and _as_utc(v.shown_at) >= now - timedelta(days=SEEN_DAYS)]

    fresh: List[PrioritizedNewsItem] = []
    for it in sorted(items, key=lambda x: x.scores.total, reverse=True):
        if any(_same_story(it, f) for f in fresh):
            continue  # duplicate within this scan
        if any(_same_story(it, r) for r in recent):
            continue  # already shown recently
        fresh.append(it.model_copy(update={"id": _story_key(it), "scanned_at": now}))

    for it in fresh:
        cache[it.id] = it
    logger.info(f"Scan: {len(items)} stories -> {len(fresh)} new (duplicates / recently shown removed)")
    return fresh


def refresh_latest(cache: Dict[str, PrioritizedNewsItem], items: List[PrioritizedNewsItem]) -> int:
    """No NEW stories in this scan: make the stories it found (already shown before) the current digest again,
    so he still gets today's latest news instead of an empty answer. Returns how many were re-dated."""
    now = datetime.now(timezone.utc)
    count = 0
    for key, cached in list(cache.items()):
        if any(_same_story(cached, it) for it in items):
            cache[key] = cached.model_copy(update={"scanned_at": now})
            count += 1
    return count


def mark_shown(cache: Dict[str, PrioritizedNewsItem], items: List[PrioritizedNewsItem]) -> None:
    """Remember which stories the user has seen, so later scans don't repeat them."""
    now = datetime.now(timezone.utc)
    for it in items:
        if it.id in cache and not cache[it.id].shown_at:
            cache[it.id] = cache[it.id].model_copy(update={"shown_at": now})


def latest_scan(cache: Dict[str, PrioritizedNewsItem]) -> List[PrioritizedNewsItem]:
    """Stories from the most recent scan that found something new, best first.
    Legacy entries without scanned_at (old positional ids) are ignored once stamped stories exist."""
    stamped = [v for v in cache.values() if v.scanned_at]
    if not stamped:
        return sorted(cache.values(), key=lambda x: x.scores.total, reverse=True)
    newest = max(_as_utc(v.scanned_at) for v in stamped)
    batch = [v for v in stamped if _as_utc(v.scanned_at) == newest]
    return sorted(batch, key=lambda x: x.scores.total, reverse=True)


def merge_news(items: List[PrioritizedNewsItem]) -> List[PrioritizedNewsItem]:
    cache = load_news()
    fresh = apply_scan(cache, items)
    save_news(cache)
    return fresh


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception as e:
        logger.warning(f"Failed to read {path.name}: {e}")
        return {}


def _write_json(path: Path, data: dict) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_caption(news_id: str) -> Optional[str]:
    return _read_json(CAPTIONS_FILE).get(news_id)


def save_caption(news_id: str, caption: str) -> None:
    data = _read_json(CAPTIONS_FILE)
    data[news_id] = caption
    _write_json(CAPTIONS_FILE, data)


def load_definition(term: str) -> Optional[dict]:
    return _read_json(DEFINITIONS_FILE).get(term.lower())


def save_definition(term: str, definition: dict) -> None:
    data = _read_json(DEFINITIONS_FILE)
    data[term.lower()] = definition
    _write_json(DEFINITIONS_FILE, data)


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
