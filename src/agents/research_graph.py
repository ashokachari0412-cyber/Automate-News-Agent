"""LangGraph workflow definition for the AI Research Agent."""

import logging
import time
from typing import Optional

from langgraph.graph import StateGraph, START, END

from config.settings import Settings, settings
from src.agents.nodes import ResearchNodes, ResearchState
from src.collectors.base import BaseCollector
from src.llm.client import LLMClient
from src.models.news import NewsRankingResult

logger = logging.getLogger(__name__)


def create_research_graph(
    collector: Optional[BaseCollector] = None,
    llm_client: Optional[LLMClient] = None,
):
    """Build and compile the LangGraph workflow for AI News Research."""
    nodes = ResearchNodes(collector=collector, llm_client=llm_client)

    builder = StateGraph(ResearchState)

    # Register nodes
    builder.add_node("collect_tweets", nodes.collect_tweets_node)
    builder.add_node("filter_noise", nodes.filter_noise_node)
    builder.add_node("cluster_tweets", nodes.cluster_tweets_node)
    builder.add_node("prioritize_and_rank", nodes.prioritize_and_rank_node)

    # Build sequential graph edges
    builder.add_edge(START, "collect_tweets")
    builder.add_edge("collect_tweets", "filter_noise")
    builder.add_edge("filter_noise", "cluster_tweets")
    builder.add_edge("cluster_tweets", "prioritize_and_rank")
    builder.add_edge("prioritize_and_rank", END)

    return builder.compile()


async def run_research_workflow(
    collector: Optional[BaseCollector] = None,
    llm_client: Optional[LLMClient] = None,
    cfg: Settings = settings,
) -> NewsRankingResult:
    """Execute the full end-to-end AI Research and Prioritization agent.

    Returns:
        NewsRankingResult containing the ranked items and top pick for Step 2 (Video Gen).
    """
    start_time = time.time()
    graph = create_research_graph(collector=collector, llm_client=llm_client)

    initial_state: ResearchState = {
        "accounts": cfg.get_trusted_accounts(),
        "raw_tweets": [],
        "filtered_tweets": [],
        "clusters": [],
        "ranked_news": [],
        "top_pick": None,
        "result": None,
        "error": None,
    }

    final_state = await graph.ainvoke(initial_state)

    result = final_state.get("result")
    if result:
        result.execution_time_seconds = round(time.time() - start_time, 2)
        return result

    # Fallback empty result
    return NewsRankingResult(
        items=[],
        total_tweets_collected=0,
        total_candidates_analyzed=0,
        execution_time_seconds=round(time.time() - start_time, 2),
    )
