"""Unit tests for Twitter and news collectors."""

import asyncio
from config.settings import settings
from src.collectors.mock_collector import MockCollector
from src.collectors.playwright_collector import clean_tweet_text, PlaywrightTwitterCollector
from src.collectors.factory import get_collector


def test_trusted_accounts_loading():
    accounts = settings.get_trusted_accounts()
    assert len(accounts) > 0
    handles = [acc.handle.lower() for acc in accounts]
    assert "sama" in handles
    assert "demishassabis" in handles
    assert "openaI".lower() in handles
    assert "anthropicai".lower() in handles


def test_clean_tweet_text():
    raw = "   Big breakthrough in    multimodal   reasoning! \n\n  Check: https://link.com  "
    cleaned = clean_tweet_text(raw)
    assert cleaned == "Big breakthrough in multimodal reasoning! Check: https://link.com"


def test_mock_collector():
    async def _run():
        collector = MockCollector()
        accounts = settings.get_trusted_accounts()
        tweets = await collector.collect(accounts, max_per_account=5, max_age_hours=48)
        assert len(tweets) > 0
        first = tweets[0]
        assert first.author_handle
        assert first.text
        assert first.engagement_score >= 0

    asyncio.run(_run())


def test_collector_factory():
    collector = get_collector(settings)
    assert collector is not None
