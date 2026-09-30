"""Twitter API v2 Collector using Tweepy and HTTPX."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import httpx
from config.settings import TwitterAccountConfig
from src.collectors.base import BaseCollector
from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)


class TwitterApiCollector(BaseCollector):
    """Fetches tweets using the official Twitter API v2."""

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ):
        self.bearer_token = bearer_token
        self.api_key = api_key
        self.api_secret = api_secret
        self._client = None
        self._init_client()

    def _init_client(self):
        try:
            import tweepy
            if self.bearer_token:
                self._client = tweepy.Client(
                    bearer_token=self.bearer_token,
                    wait_on_rate_limit=True
                )
            elif self.api_key and self.api_secret:
                self._client = tweepy.Client(
                    consumer_key=self.api_key,
                    consumer_secret=self.api_secret,
                    wait_on_rate_limit=True
                )
        except Exception as e:
            logger.warning(f"Failed to initialize Tweepy client: {e}")

    async def collect(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 5,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        """Collect tweets from Twitter API v2."""
        if not self._client and not self.bearer_token:
            logger.error("Twitter API collector called without valid credentials.")
            return []

        results: List[RawTweet] = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        for account in accounts:
            try:
                account_tweets = await self._fetch_account_tweets(
                    account=account,
                    max_results=max_per_account,
                    cutoff_time=cutoff_time
                )
                results.extend(account_tweets)
            except Exception as e:
                logger.error(f"Error fetching tweets for @{account.handle} via API: {e}")

        logger.info(f"Twitter API v2 collector retrieved {len(results)} tweets across {len(accounts)} accounts.")
        return results

    async def _fetch_account_tweets(
        self,
        account: TwitterAccountConfig,
        max_results: int,
        cutoff_time: datetime
    ) -> List[RawTweet]:
        """Fetch recent tweets for a single handle."""
        tweets: List[RawTweet] = []

        if self._client:
            # Use Tweepy client
            user_resp = self._client.get_user(username=account.handle)
            if not user_resp or not user_resp.data:
                return []
            user_id = user_resp.data.id

            timeline_resp = self._client.get_users_tweets(
                id=user_id,
                max_results=max(5, min(max_results, 100)),
                tweet_fields=["created_at", "public_metrics", "entities"],
                exclude=["retweets"]
            )

            if not timeline_resp or not timeline_resp.data:
                return []

            for t in timeline_resp.data:
                created_at = t.created_at
                if created_at and created_at < cutoff_time:
                    continue

                metrics = getattr(t, "public_metrics", {}) or {}
                urls = []
                entities = getattr(t, "entities", {}) or {}
                if "urls" in entities:
                    urls = [u.get("expanded_url", "") for u in entities["urls"] if u.get("expanded_url")]

                tweet_item = RawTweet(
                    id=str(t.id),
                    author_handle=account.handle,
                    author_name=account.name,
                    text=t.text,
                    created_at=created_at or datetime.now(timezone.utc),
                    likes=metrics.get("like_count", 0),
                    retweets=metrics.get("retweet_count", 0),
                    replies=metrics.get("reply_count", 0),
                    views=metrics.get("impression_count"),
                    urls=urls,
                    source_url=f"https://x.com/{account.handle}/status/{t.id}",
                    author_weight=account.weight,
                    account_category=account.category,
                )
                tweets.append(tweet_item)

        return tweets
