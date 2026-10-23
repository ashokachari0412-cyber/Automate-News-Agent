"""Second news source: Gemini with Google Search grounding.

Finds important AI news from the last N hours that the official feeds may have missed. Results are returned
as RawTweet candidates so they go through the same filter, clustering and prioritization as the feeds.
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)

GEMINI_HANDLE = "gemini_search"

SEARCH_PROMPT = """Use Google Search to find the most important AI news published in the last {hours} hours
(today is {today}): new AI models, tools, research, product launches and big company announcements.

Already known (do NOT repeat these):
{known}

Return ONLY a JSON array (no markdown) of up to {limit} items, most important first:
[{{"title": "...", "summary": "2-3 factual sentences", "url": "original article URL",
   "source": "publisher name", "published": "YYYY-MM-DD"}}]
Only include stories you found in search results from the last {hours} hours. If none, return []."""


def _parse_items(text: str) -> List[dict]:
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
        return [d for d in data if isinstance(d, dict) and d.get("title")]
    except json.JSONDecodeError:
        return []


def _search_sync(hours: int, known_titles: List[str], limit: int) -> List[RawTweet]:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback

    prompt = SEARCH_PROMPT.format(
        hours=hours, today=datetime.now(timezone.utc).strftime("%Y-%m-%d"), limit=limit,
        known="\n".join(f"- {t}" for t in known_titles[:40]) or "(none)")
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2)
    resp = generate_with_fallback(prompt, config)

    # Real source links from the grounding metadata, used when the model's own URL is missing.
    grounded = []
    try:
        for chunk in resp.candidates[0].grounding_metadata.grounding_chunks or []:
            if chunk.web and chunk.web.uri:
                grounded.append(chunk.web.uri)
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    tweets: List[RawTweet] = []
    for i, item in enumerate(_parse_items(resp.text)[:limit]):
        try:
            published = datetime.strptime(str(item.get("published", ""))[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            published = now
        if published < cutoff - timedelta(hours=24):  # date-only precision: allow the previous calendar day
            continue
        url = str(item.get("url") or "") or (grounded[i] if i < len(grounded) else "")
        title, summary = str(item["title"]).strip(), str(item.get("summary", "")).strip()
        tweets.append(RawTweet(
            id="gs_" + hashlib.sha1((url or title).encode()).hexdigest()[:12],
            author_handle=GEMINI_HANDLE,
            author_name=str(item.get("source") or "Google Search").strip(),
            text=f"{title}. {summary}",
            created_at=min(published, now),
            urls=[url] if url else [],
            source_url=url,
            author_weight=1.0,
            account_category="gemini_search",
        ))
    return tweets


async def search_ai_news(hours: int = 24, known_titles: Optional[List[str]] = None, limit: int = 8) -> List[RawTweet]:
    """Never raises: on quota/network errors the scan simply continues with the official feeds."""
    try:
        items = await asyncio.to_thread(_search_sync, hours, known_titles or [], limit)
        logger.info(f"Gemini + Google Search found {len(items)} extra candidates.")
        return items
    except Exception as e:
        logger.warning(f"Gemini news search skipped: {e}")
        return []
