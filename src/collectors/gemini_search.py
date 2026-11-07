"""Second news source: Gemini with Google Search grounding.

Finds important AI news from the last N hours that the official feeds may have missed. Results are returned
as RawTweet candidates so they go through the same filter, clustering and prioritization as the feeds.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)

GEMINI_HANDLE = "gemini_search"

SEARCH_PROMPT = """Use Google Search to find the most important AI news published in the last {hours} hours
(today is {today}): {focus}.
Skip minor corporate PR (partnership press releases, 'centers of excellence', awards).

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


DEFAULT_FOCUS = "new AI models, tools, research, product launches and big company announcements"

# Google-only scans: one search per area, run together, so the results cover more than one search can.
WIDE_FOCUSES = [
    "new AI model releases and major updates from AI labs (OpenAI, Google, Anthropic, Meta, xAI, Mistral, DeepSeek, Qwen)",
    "new AI tools, apps and product features people can use (video, image, coding, agents, assistants)",
    "important AI research papers and open-source AI model or framework releases",
    "big AI business news: funding, acquisitions, chips and AI hardware, regulation",
    "viral AI and tech moments: demos, controversies or posts blowing up on X, Reddit or YouTube",
    "consumer tech launches: new phones, laptops, wearables, gadgets and EVs (Apple, Samsung, Google Pixel and others)",
    "big non-AI tech news: major outages, cybersecurity incidents, space tech, tech layoffs",
    "AI and tech news from India: Indian AI startups, big Indian tech companies' AI moves, government AI rules",
    "new features on Instagram, YouTube, TikTok and other creator platforms that content creators should know about",
]


def _search_sync(hours: int, known_titles: List[str], limit: int, focus: str = DEFAULT_FOCUS) -> List[RawTweet]:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback

    prompt = SEARCH_PROMPT.format(
        hours=hours, today=datetime.now(timezone.utc).strftime("%Y-%m-%d"), limit=limit, focus=focus,
        known="\n".join(f"- {t}" for t in known_titles[:40]) or "(none)")
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2)
    resp = generate_with_fallback(prompt, config, wait=False)

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


# Out of quota: skip the search for a while instead of slowing every scan down with failing calls.
_QUOTA_SKIP_SECONDS = 60 * 60
_skip_until = 0.0


async def search_ai_news(hours: int = 24, known_titles: Optional[List[str]] = None, limit: int = 8) -> List[RawTweet]:
    """Never raises: on quota/network errors the scan simply continues with the official feeds."""
    global _skip_until
    if time.monotonic() < _skip_until:
        logger.info(f"Gemini news search skipped (out of quota, retrying in {int(_skip_until - time.monotonic()) // 60} min)")
        return []
    try:
        items = await asyncio.to_thread(_search_sync, hours, known_titles or [], limit)
        logger.info(f"Gemini + Google Search found {len(items)} extra candidates.")
        return items
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            _skip_until = time.monotonic() + _QUOTA_SKIP_SECONDS
        logger.warning(f"Gemini news search skipped: {str(e)[:200]}")
        return []


async def search_ai_news_wide(hours: int = 24, limit_each: int = 6) -> List[RawTweet]:
    """Google-only scan: one search per area (models, tools, research/open source, business/hardware) in parallel,
    merged without duplicates. Never raises."""
    global _skip_until
    if time.monotonic() < _skip_until:
        logger.info("Gemini news search skipped (out of quota)")
        return []
    results = await asyncio.gather(*[asyncio.to_thread(_search_sync, hours, [], limit_each, f) for f in WIDE_FOCUSES],
                                   return_exceptions=True)
    merged, seen = [], set()
    for res in results:
        if isinstance(res, Exception):
            if "429" in str(res) or "RESOURCE_EXHAUSTED" in str(res):
                _skip_until = time.monotonic() + _QUOTA_SKIP_SECONDS
            logger.warning(f"One Google news search failed: {str(res)[:160]}")
            continue
        for t in res:
            key = re.sub(r"[^a-z0-9]+", " ", t.text.split(". ")[0].lower()).strip()[:60]
            if t.id not in seen and key not in seen:
                seen.update({t.id, key})
                merged.append(t)
    logger.info(f"Gemini + Google Search ({len(WIDE_FOCUSES)} areas) found {len(merged)} stories.")
    return merged
