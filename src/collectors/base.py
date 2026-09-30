"""Base collector interface for tweet ingestion."""

from abc import ABC, abstractmethod
from typing import List
from config.settings import TwitterAccountConfig
from src.models.tweet import RawTweet


class BaseCollector(ABC):
    """Abstract collector for retrieving posts from trusted sources."""

    @abstractmethod
    async def collect(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 5,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        """Collect tweets from the specified accounts within the given age window.

        Args:
            accounts: List of account configurations to query.
            max_per_account: Maximum number of recent posts per account.
            max_age_hours: Cutoff age in hours.

        Returns:
            List of normalized RawTweet objects.
        """
        pass
