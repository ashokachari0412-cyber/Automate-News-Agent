"""Data models for raw and processed tweet items."""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field


class RawTweet(BaseModel):
    """Normalized schema for a tweet collected from any source."""
    id: str
    author_handle: str
    author_name: str
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    views: Optional[int] = None
    urls: List[str] = Field(default_factory=list)
    media_urls: List[str] = Field(default_factory=list)
    source_url: str = ""
    author_weight: float = 1.0
    account_category: str = "general"

    @property
    def engagement_score(self) -> float:
        """Calculates a normalized engagement indicator."""
        return float(self.likes + (self.retweets * 2) + (self.replies * 3))


class TweetCluster(BaseModel):
    """Cluster of related tweets covering the same news event."""
    cluster_id: str
    headline_hint: str
    tweets: List[RawTweet] = Field(default_factory=list)
    primary_handle: str = ""
    combined_weight: float = 1.0
