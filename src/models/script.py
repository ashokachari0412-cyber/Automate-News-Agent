"""Data models for AI Video Scripts, Scene Clips, and Avatar Blueprints."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class SceneClip(BaseModel):
    """A single visual scene or cut in the 35-40s short video."""
    scene_number: int = Field(..., description="Sequential scene index (1, 2, 3...)")
    timestamp: str = Field(..., description="e.g. '00:00 - 00:08'")
    duration_seconds: float = Field(..., description="Duration of this clip in seconds")
    spoken_script: str = Field(..., description="Exact voiceover words spoken by avatar")
    visual_description: str = Field(..., description="Visual framing: B-roll, screen capture, avatar motion")
    on_screen_text: str = Field(default="", description="Bold text caption overlay for short-form retention")


class VideoBlueprint(BaseModel):
    """Complete blueprint for 35-40 second short-form avatar video."""
    news_id: str
    title: str
    hook: str = Field(..., description="Irresistible 0-5s opening hook")
    estimated_duration_seconds: int = Field(default=3, ge=1, le=60)
    scenes: List[SceneClip] = Field(default_factory=list)
    avatar_profile: str = Field(
        default="Tech Founder (Professional / AI Pioneer)",
        description="Active avatar persona"
    )
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    primary_source_handle: str = ""
    source_url: str = ""
    output_video_path: Optional[str] = None
