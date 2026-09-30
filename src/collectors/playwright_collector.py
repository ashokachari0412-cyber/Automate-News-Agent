"""Playwright-based Twitter Collector using authenticated session cookies.

Directly adapted from user's production scraping script with strict 24-hour filtering,
pinned tweet detection, bad keyword filtering, and AI relevance checks.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from config.settings import TwitterAccountConfig, settings
from src.collectors.base import BaseCollector
from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)

BAD_KEYWORDS = [
    "follow me",
    "subscribe",
    "newsletter",
    "dm me",
    "hiring",
    "course",
    "webinar",
    "join us",
]

AI_KEYWORDS = [
    "ai",
    "model",
    "gpt",
    "claude",
    "gemini",
    "agent",
    "llm",
    "open source",
    "reasoning",
    "video",
    "voice",
    "coding",
    "api",
    "automation",
    "multimodal",
    "inference",
]


def clean_tweet_text(text: str) -> str:
    """Normalize whitespace and strip raw tweet text."""
    return re.sub(r"\s+", " ", text).strip()


class PlaywrightTwitterCollector(BaseCollector):
    """Scrapes Twitter using Playwright with persistent session cookies and strict filtering."""

    def __init__(
        self,
        cookies_path: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        headless: bool = True,
    ):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.cookies_path = cookies_path or str(base_dir / "cookies.json")
        self.user_data_dir = user_data_dir or str(base_dir / "userdata")
        self.headless = headless

    async def collect(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 2,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        """Run the synchronous Playwright scraper in an async executor thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._scrape_sync,
            accounts,
            max_per_account,
            max_age_hours,
        )

    def _scrape_sync(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 2,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        """Execute Playwright scraping with cookie authentication and strict filtering."""
        from playwright.sync_api import sync_playwright
        import time

        posts: List[RawTweet] = []
        os.makedirs(self.user_data_dir, exist_ok=True)

        logger.info(f"Launching Playwright browser (headless={self.headless})...")

        with sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                logger.error(f"Failed to launch browser context: {e}")
                return []

            page = context.new_page()

            # Sequence from user's script: navigate to x.com -> inject cookies -> go to x.com/home
            try:
                page.goto("https://x.com", timeout=20000)
                time.sleep(2)

                if os.path.exists(self.cookies_path):
                    with open(self.cookies_path, "r", encoding="utf-8") as f:
                        cookies = json.load(f)

                    for cookie in cookies:
                        cookie.pop("sameSite", None)
                        cookie.pop("hostOnly", None)
                        cookie.pop("session", None)
                        cookie.pop("storeId", None)
                        cookie["domain"] = ".x.com"

                    context.add_cookies(cookies)
                    logger.info("Loaded and injected cookies into browser context.")

                page.goto("https://x.com/home", timeout=20000)
                time.sleep(3)
            except Exception as e:
                logger.warning(f"Initial setup navigation encountered: {e}")

            for acc in accounts:
                handle = acc.handle
                is_official = acc.category == "official_lab"

                logger.info(f"Reading @{handle}...")
                success = False

                for url_target in [f"https://x.com/{handle}?f=live", f"https://x.com/{handle}"]:
                    try:
                        page.goto(url_target, timeout=20000)
                        page.wait_for_selector('article[data-testid="tweet"]', timeout=8000)
                        time.sleep(2)
                        success = True
                        break
                    except Exception:
                        continue

                if not success:
                    logger.info(f"Skipping @{handle} (no active tweets rendered or restricted).")
                    continue

                try:
                    articles = page.locator('article[data-testid="tweet"]').all()[:10]
                except Exception as e:
                    logger.debug(f"Could not locate tweet articles for @{handle}: {e}")
                    continue

                account_posts_count = 0
                now = datetime.now(timezone.utc)

                for article in articles:
                    try:
                        # 1. Skip pinned tweets
                        article_text = article.inner_text().lower()
                        if "pinned" in article_text:
                            continue

                        # 2. Extract tweet text
                        tweet_locators = article.locator('[data-testid="tweetText"]')
                        if tweet_locators.count() > 0:
                            raw_text = tweet_locators.first.inner_text()
                        else:
                            raw_text = article.inner_text()

                        cleaned = clean_tweet_text(raw_text)

                        # 3. Strict 24-hour time check
                        time_element = article.locator("time").first
                        tweet_datetime = time_element.get_attribute("datetime")
                        if not tweet_datetime:
                            continue

                        tweet_time = datetime.fromisoformat(tweet_datetime.replace("Z", "+00:00"))
                        age_hours = (now - tweet_time).total_seconds() / 3600

                        if age_hours > max_age_hours:
                            continue

                        # 4. Basic filters
                        if cleaned.startswith("@"):
                            continue
                        if len(cleaned.split()) < 5:
                            continue

                        lower = cleaned.lower()
                        if any(k in lower for k in BAD_KEYWORDS):
                            continue

                        # 5. AI relevance filter
                        ai_related = any(w in lower for w in AI_KEYWORDS)
                        if not ai_related and not is_official:
                            continue

                        # 6. Extract tweet link
                        links = article.locator("a").evaluate_all("els => els.map(e => e.href)")
                        tweet_link = ""
                        for link in links:
                            if "/status/" in link:
                                tweet_link = link
                                break

                        if not tweet_link:
                            continue

                        # 7. Deduplication
                        if any(p.text == cleaned for p in posts):
                            continue

                        tweet_id = tweet_link.split("/status/")[-1].split("?")[0] if "/status/" in tweet_link else f"pw_{handle}_{account_posts_count}"

                        tweet_obj = RawTweet(
                            id=tweet_id,
                            author_handle=handle,
                            author_name=acc.name,
                            text=cleaned,
                            created_at=tweet_time,
                            urls=[tweet_link],
                            source_url=tweet_link,
                            author_weight=acc.weight,
                            account_category=acc.category,
                        )

                        posts.append(tweet_obj)
                        account_posts_count += 1
                        logger.info(f"Added post from @{handle}: {cleaned[:60]}...")

                        if account_posts_count >= max_per_account:
                            break

                    except Exception as e:
                        logger.debug(f"Article parsing error for @{handle}: {e}")

            context.close()

        logger.info(f"Playwright collector retrieved {len(posts)} verified posts.")
        return posts
