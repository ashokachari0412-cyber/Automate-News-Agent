"""Unit tests for the AI Research and Prioritization LangGraph workflow."""

import asyncio
from src.agents.research_graph import run_research_workflow
from src.collectors.mock_collector import MockCollector
from src.models.news import PrioritizedNewsItem, NewsScoreBreakdown


def test_score_breakdown():
    score = NewsScoreBreakdown(
        impact=25.0,
        novelty=24.0,
        credibility=25.0,
        video_adaptability=23.0,
        total=97.0
    )
    assert score.total == 97.0
    assert score.impact == 25.0


def test_end_to_end_research_pipeline():
    async def _run():
        mock_collector = MockCollector()
        result = await run_research_workflow(collector=mock_collector)

        assert result is not None
        assert len(result.items) > 0
        assert result.top_pick is not None

        top = result.top_pick
        assert top.scores.total >= 70.0
        assert len(top.key_bullet_points) > 0
        assert top.hook != ""
        assert top.suggested_video_angle != ""

    asyncio.run(_run())
