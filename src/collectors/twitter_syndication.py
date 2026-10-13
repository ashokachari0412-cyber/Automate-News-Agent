"""Public Twitter Syndication and Feed Collector.

Allows collecting recent posts from public Twitter handles without requiring
a paid Twitter API Developer subscription.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List
import httpx
from bs4 import BeautifulSoup

from config.settings import TwitterAccountConfig
from src.collectors.base import BaseCollector
from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class TwitterSyndicationCollector(BaseCollector):
    """Collects tweets using Twitter's public embed/syndication endpoints."""

    async def collect(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 5,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        """Query syndication endpoints concurrently for high-speed collection."""
        results: List[RawTweet] = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        # Prioritize top frontier accounts first for speed
        target_accounts = accounts[:6]

        async with httpx.AsyncClient(headers=HEADERS, timeout=4.0, follow_redirects=True) as client:
            async def _safe_fetch(acc):
                try:
                    return await self._fetch_syndication(
                        client=client,
                        account=acc,
                        max_results=max_per_account,
                        cutoff_time=cutoff_time
                    )
                except Exception as e:
                    logger.debug(f"Syndication fetch for @{acc.handle} notice: {e}")
                    return []

            fetched_lists = await asyncio.gather(*[_safe_fetch(a) for a in target_accounts])
            for tweets in fetched_lists:
                results.extend(tweets)

        logger.info(f"Syndication collector retrieved {len(results)} tweets.")
        return results

    async def _fetch_syndication(
        self,
        client: httpx.AsyncClient,
        account: TwitterAccountConfig,
        max_results: int,
        cutoff_time: datetime
    ) -> List[RawTweet]:
        """Scrape timeline from Twitter syndication profile service."""
        url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{account.handle}"
        resp = await client.get(url)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        next_data_script = soup.find("script", id="__NEXT_DATA__")

        tweets: List[RawTweet] = []
        if next_data_script and next_data_script.string:
            try:
                data = json.loads(next_data_script.string)
                entries = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("timeline", {})
                    .get("entries", [])
                )

                for entry in entries:
                    if len(tweets) >= max_results:
                        break

                    content = entry.get("content", {}).get("tweet", {})
                    if not content:
                        continue

                    tweet_id = str(content.get("id_str", ""))
                    text = content.get("text", "")
                    created_at_str = content.get("created_at")

                    # Parse twitter date format: "Wed Oct 10 20:19:24 +0000 2018"
                    created_at = datetime.now(timezone.utc)
                    if created_at_str:
                        try:
                            created_at = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
                        except Exception:
                            pass

                    if created_at < cutoff_time:
                        continue

                    likes = content.get("favorite_count", 0)
                    retweets = content.get("retweet_count", 0)
                    urls = [u.get("expanded_url", "") for u in content.get("entities", {}).get("urls", [])]

                    tweets.append(
                        RawTweet(
                            id=tweet_id or f"syn_{account.handle}_{len(tweets)}",
                            author_handle=account.handle,
                            author_name=account.name,
                            text=text,
                            created_at=created_at,
                            likes=likes,
                            retweets=retweets,
                            urls=urls,
                            source_url=f"https://x.com/{account.handle}/status/{tweet_id}" if tweet_id else f"https://x.com/{account.handle}",
                            author_weight=account.weight,
                            account_category=account.category,
                        )
                    )
            except Exception as e:
                logger.debug(f"Could not parse __NEXT_DATA__ for @{account.handle}: {e}")

        # Fallback to HTML elements if script tag not found
        if not tweets:
            tweet_elements = soup.select("article, [data-testid='tweet']")
            for idx, el in enumerate(tweet_elements[:max_results]):
                text_el = el.select_one("div[lang]") or el
                text = text_el.get_text(separator=" ", strip=True)
                if len(text) > 30:
                    tweets.append(
                        RawTweet(
                            id=f"html_{account.handle}_{idx}",
                            author_handle=account.handle,
                            author_name=account.name,
                            text=text,
                            created_at=datetime.now(timezone.utc),
                            source_url=f"https://x.com/{account.handle}",
                            author_weight=account.weight,
                            account_category=account.category,
                        )
                    )

        return tweets
