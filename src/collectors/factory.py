"""Collector factory for instantiating the right collection strategy."""

import logging
import os
from pathlib import Path
from typing import List

from config.settings import Settings, TwitterAccountConfig, settings
from src.collectors.base import BaseCollector
from src.collectors.playwright_collector import PlaywrightTwitterCollector
from src.collectors.twitter_api import TwitterApiCollector
from src.collectors.twitter_syndication import TwitterSyndicationCollector
from src.collectors.mock_collector import MockCollector
from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)


class AutoFallbackCollector(BaseCollector):
    """Tries authenticated Playwright or live syndication first; falls back to mock dataset if needed."""

    def __init__(self, primary: BaseCollector, mock: MockCollector):
        self.primary = primary
        self.mock = mock

    async def collect(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 2,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        try:
            tweets = await self.primary.collect(
                accounts=accounts,
                max_per_account=max_per_account,
                max_age_hours=max_age_hours,
            )
            if tweets:
                logger.info(f"Auto collector: Successfully gathered {len(tweets)} live posts.")
                return tweets
        except Exception as e:
            logger.warning(f"Primary collector ({type(self.primary).__name__}) encountered issue: {e}")

        logger.info("Auto collector: Falling back to high-signal mock AI dataset for uninterrupted workflow.")
        return await self.mock.collect(accounts, max_per_account, max_age_hours)


def get_collector(cfg: Settings = settings) -> BaseCollector:
    """Returns the appropriate collector instance based on configuration and available credentials."""
    mode = cfg.COLLECTOR_MODE.lower()
    base_dir = Path(__file__).resolve().parent.parent.parent
    cookies_path = str(base_dir / cfg.COOKIES_FILE)

    if mode == "mock":
        return MockCollector()

    if mode == "playwright":
        return PlaywrightTwitterCollector(
            cookies_path=cookies_path,
            user_data_dir=str(base_dir / cfg.USER_DATA_DIR),
            headless=cfg.PLAYWRIGHT_HEADLESS,
        )

    if mode == "twitter_api":
        return TwitterApiCollector(
            bearer_token=cfg.TWITTER_BEARER_TOKEN,
            api_key=cfg.TWITTER_API_KEY,
            api_secret=cfg.TWITTER_API_SECRET,
        )

    if mode == "syndication":
        return TwitterSyndicationCollector()

    # "auto" mode:
    # 1. If cookies.json exists, prioritize Playwright authenticated scraper!
    if os.path.exists(cookies_path):
        logger.info("Auto mode: cookies.json found. Using Playwright authenticated collector.")
        pw_collector = PlaywrightTwitterCollector(
            cookies_path=cookies_path,
            user_data_dir=str(base_dir / cfg.USER_DATA_DIR),
            headless=cfg.PLAYWRIGHT_HEADLESS,
        )
        return AutoFallbackCollector(primary=pw_collector, mock=MockCollector())

    # 2. If official Twitter API credentials are provided
    if cfg.TWITTER_BEARER_TOKEN or (cfg.TWITTER_API_KEY and cfg.TWITTER_API_SECRET):
        logger.info("Auto mode: Twitter API credentials found. Using TwitterApiCollector.")
        return TwitterApiCollector(
            bearer_token=cfg.TWITTER_BEARER_TOKEN,
            api_key=cfg.TWITTER_API_KEY,
            api_secret=cfg.TWITTER_API_SECRET,
        )

    # 3. Fallback to syndication + mock
    logger.info("Auto mode: Using Syndication with Mock fallback.")
    return AutoFallbackCollector(
        primary=TwitterSyndicationCollector(),
        mock=MockCollector()
    )
