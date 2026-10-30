"""Data models for prioritized AI news items and pipeline results."""

import re
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
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
    published_at: Optional[datetime] = Field(default=None, description="Actual publication timestamp from source")
    vocabulary: Dict[str, str] = Field(default_factory=dict, description="Simple meanings of technical terms in this update")
    image_prompt: str = Field(default="", description="Detailed, high-tech prompt to generate an image tailored to this exact news update")
    scanned_at: Optional[datetime] = Field(default=None, description="When the scan that found this story ran (digest = latest scan only)")
    shown_at: Optional[datetime] = Field(default=None, description="When this story was first shown to the user in a digest")
    origin: str = Field(default="Official feed", description="Where the story came from: Official feed, Gemini + Google Search, or both")
    source_name: str = Field(default="", description="Publisher / feed name, e.g. OpenAI Official or TechCrunch")

    def source_note(self) -> str:
        """Caption lines naming where the news came from (with the link) and when it was published.
        No published time from the feed: the date in the link (/2026/10/30/) is used if there is one."""
        url = next((u for u in self.source_urls if u.startswith("http")), "")
        name = self.source_name or (f"@{self.primary_source_handle.lstrip('@')}" if self.primary_source_handle else "")
        when = self.published_at
        if not when and url:
            m = re.search(r"/(20\d\d)/(\d{1,2})/(\d{1,2})/", url)
            if m:
                try:
                    when = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    when = None
        lines = []
        if name or url:
            lines.append("📰 Source: " + " · ".join(p for p in (name, url) if p))
        if when:
            lines.append(f"🗓️ Published: {when.day} {when.strftime('%b %Y')}")
        return "\n".join(lines)


def with_source(caption: str, note: str) -> str:
    """Puts the source lines before the hashtag line (or at the end), unless they're already there."""
    caption = (caption or "").rstrip()
    if not note or "📰 Source:" in caption:
        return caption
    lines = caption.split("\n")
    if lines and lines[-1].strip().startswith("#"):
        return "\n".join(lines[:-1]).rstrip() + f"\n\n{note}\n\n{lines[-1].strip()}"
    return f"{caption}\n\n{note}"


class NewsRankingResult(BaseModel):
    """Overall deliverable produced by the AI Research Agent workflow."""
    items: List[PrioritizedNewsItem] = Field(default_factory=list)
    total_tweets_collected: int = 0
    total_candidates_analyzed: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    top_pick: Optional[PrioritizedNewsItem] = None
    execution_time_seconds: float = 0.0
