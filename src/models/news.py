"""Data models for prioritized AI news items and pipeline results."""

from datetime import datetime, timezone
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class NewsScoreBreakdown(BaseModel):
    """Detailed breakdown of prioritization scoring (each out of 25 pts, total 100)."""
    impact: float = Field(..., ge=0, le=25, description="Real-world technical and industry significance (0-25)")
    novelty: float = Field(..., ge=0, le=25, description="Freshness, unexpectedness, or paradigm shift (0-25)")
    credibility: float = Field(..., ge=0, le=25, description="Source reliability and verifiable proof (0-25)")
    video_adaptability: float = Field(..., ge=0, le=25, description="Suitability for a fast 40-45s visual avatar video (0-25)")
    total: float = Field(..., ge=0, le=100, description="Overall priority score")


class PrioritizedNewsItem(BaseModel):
    """A prioritized AI news story formatted for human review and video generation."""
    id: str
    title: str = Field(..., description="Crisp, punchy news headline")
    hook: str = Field(..., description="High-retention 1-sentence opening hook for short video")
    summary: str = Field(..., description="2-3 sentence clear summary of what happened")
    key_bullet_points: List[str] = Field(
        default_factory=list,
        description="3-4 crisp technical and practical takeaways"
    )
    category: str = Field(
        ...,
        description="e.g. Frontier Model, Research Breakthrough, Open Source, AI Hardware, Agentic Workflow"
    )
    scores: NewsScoreBreakdown
    priority_tier: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    why_it_matters: str = Field(..., description="Why viewers/developers should care right now")
    suggested_video_angle: str = Field(
        ...,
        description="Creative framing and visual cues for 40-45 second avatar video"
    )
    primary_source_handle: str = Field(default="", description="Twitter handle that broke or led the news")
    source_urls: List[str] = Field(default_factory=list, description="Original tweet links or referenced URLs")
    source_tweet_ids: List[str] = Field(default_factory=list)


class NewsRankingResult(BaseModel):
    """Overall deliverable produced by the AI Research Agent workflow."""
    items: List[PrioritizedNewsItem] = Field(default_factory=list)
    total_tweets_collected: int = 0
    total_candidates_analyzed: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    top_pick: Optional[PrioritizedNewsItem] = None
    execution_time_seconds: float = 0.0
