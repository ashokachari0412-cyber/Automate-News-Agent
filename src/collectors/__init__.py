"""Collectors package for Twitter and news collection."""

from src.collectors.base import BaseCollector
from src.collectors.authorized_collector import AuthorizedNewsCollector
from src.collectors.playwright_collector import PlaywrightTwitterCollector
from src.collectors.twitter_api import TwitterApiCollector
from src.collectors.mock_collector import MockCollector
from src.collectors.factory import get_collector

__all__ = [
    "BaseCollector",
    "AuthorizedNewsCollector",
    "PlaywrightTwitterCollector",
    "TwitterApiCollector",
    "MockCollector",
    "get_collector",
]
