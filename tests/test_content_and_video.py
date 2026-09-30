"""Unit tests for Content Agent and Google Video Generator."""

import asyncio
import os
import pytest
from src.agents.content_agent import ContentAgent
from src.models.news import PrioritizedNewsItem, NewsScoreBreakdown
from src.video.generator import VideoGenerator


def test_video_generation_pipeline():
    async def _run():
        item = PrioritizedNewsItem(
            id="test_news_01",
            title="Google Unveils Gemini 2.5 Flash with Real-Time Reasoning",
            hook="Google just dropped Gemini 2.5 Flash with built-in reasoning.",
            summary="Google DeepMind has officially released Gemini 2.5 Flash Thinking with native multimodal reasoning.",
            key_bullet_points=[
                "Features dynamic test-time compute to search reasoning trees",
                "Maintains a 1-million-token native context window",
                "3x lower latency on complex benchmarks"
            ],
            category="Frontier Model",
            scores=NewsScoreBreakdown(impact=24.5, novelty=23.0, credibility=25.0, video_adaptability=24.0, total=96.5),
            priority_tier="HIGH",
            why_it_matters="Drastically reduces the cost of autonomous agent workflows.",
            suggested_video_angle="Side-by-side benchmark comparison with avatar narration.",
            primary_source_handle="GoogleDeepMind",
            source_urls=["https://deepmind.google/technologies/gemini"],
        )

        content_agent = ContentAgent()
        blueprint = await content_agent.generate_blueprint(news_item=item)

        assert blueprint is not None
        assert 5 <= blueprint.estimated_duration_seconds <= 60
        assert len(blueprint.scenes) > 0
        assert blueprint.hook != ""

        # Test video rendering
        video_gen = VideoGenerator()
        video_path = await video_gen.generate_video(blueprint)

        assert os.path.exists(video_path)
        assert os.path.getsize(video_path) > 1000  # Valid MP4 video created!

    asyncio.run(_run())
