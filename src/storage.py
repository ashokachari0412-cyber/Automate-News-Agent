"""Shared on-disk persistence: news cache and Alfred conversation memory."""

import json
import logging
from pathlib import Path
from typing import Dict, List

from src.models.news import PrioritizedNewsItem

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
NEWS_CACHE_FILE = ARTIFACTS_DIR / "news_cache.json"
CHAT_MEMORY_FILE = ARTIFACTS_DIR / "chat_memory.json"


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
    CHAT_MEMORY_FILE.write_text(json.dumps(memory, indent=2, default=str), encoding="utf-8")
