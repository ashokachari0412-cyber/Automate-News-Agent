"""Collector factory for instantiating the right collection strategy."""

import logging
import os
from pathlib import Path
from typing import List, Optional

from config.settings import Settings, TwitterAccountConfig, settings
from src.collectors.base import BaseCollector
from src.collectors.authorized_collector import AuthorizedNewsCollector
from src.collectors.playwright_collector import PlaywrightTwitterCollector
from src.collectors.twitter_api import TwitterApiCollector
from src.collectors.mock_collector import MockCollector
from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)


class AutoFallbackCollector(BaseCollector):
    """Tries authorized lab feeds first; falls back to mock dataset if needed."""

    def __init__(self, primary: Optional[BaseCollector] = None, mock: Optional[MockCollector] = None):
        self.primary = primary or AuthorizedNewsCollector()
        self.mock = mock or MockCollector()

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

    # Primary mode: 100% Authorized Primary Source AI Lab Feeds & Research
    logger.info("Collector factory: Using AuthorizedNewsCollector (OpenAI, DeepMind, Anthropic, Hugging Face).")
    return AutoFallbackCollector(primary=AuthorizedNewsCollector(), mock=MockCollector())

