"""Mock Collector providing realistic, high-signal AI tweets for testing and evaluation."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List
from config.settings import TwitterAccountConfig
from src.collectors.base import BaseCollector
from src.models.tweet import RawTweet

logger = logging.getLogger(__name__)

# Sample realistic AI news dataset reflecting frontier ecosystem developments
SAMPLE_TWEETS_DATA = [
    {
        "handle": "demishassabis",
        "name": "Demis Hassabis",
        "category": "pioneer_researcher",
        "weight": 1.5,
        "text": (
            "Excited to share Gemini 2.5 Flash Thinking is rolling out to developers today! "
            "Native multimodal reasoning with dynamic test-time compute, 1M context window, "
            "and 3x lower latency on complex mathematical & coding benchmarks. "
            "Paper and API docs live now: deepmind.google/technologies/gemini"
        ),
        "hours_ago": 3,
        "likes": 14200,
        "retweets": 3100,
        "replies": 840,
        "urls": ["https://deepmind.google/technologies/gemini"],
    },
    {
        "handle": "GoogleDeepMind",
        "name": "Google DeepMind",
        "category": "official_lab",
        "weight": 1.5,
        "text": (
            "Introducing Veo 3.1: Our state-of-the-art cinematic video generation model. "
            "Features native 4K consistency, physics-informed motion dynamics, and direct "
            "dialogue lip-syncing for avatars. Available to Google Workspace Labs users starting next week."
        ),
        "hours_ago": 5,
        "likes": 21500,
        "retweets": 5600,
        "replies": 1200,
        "urls": ["https://deepmind.google/research/veo-3"],
    },
    {
        "handle": "sama",
        "name": "Sam Altman",
        "category": "pioneer_researcher",
        "weight": 1.5,
        "text": (
            "Operator agents preview is expanding to all Pro subscribers this morning. "
            "Autonomous browser control across complex multi-step workflows with strict safety boundaries. "
            "Still early, but the curve is steep. Feedback welcome!"
        ),
        "hours_ago": 8,
        "likes": 32000,
        "retweets": 6800,
        "replies": 2900,
        "urls": ["https://openai.com/index/introducing-operator/"],
    },
    {
        "handle": "OpenAI",
        "name": "OpenAI",
        "category": "official_lab",
        "weight": 1.5,
        "text": (
            "Full release notes for Operator: Works across web browsers, handles auth-protected portals, "
            "and executes end-to-end tasks like booking flights, submitting expense reports, and cross-checking data. "
            "API endpoint rolling out in private beta."
        ),
        "hours_ago": 7,
        "likes": 18400,
        "retweets": 4100,
        "replies": 950,
        "urls": ["https://openai.com/blog/operator-notes"],
    },
    {
        "handle": "AnthropicAI",
        "name": "Anthropic",
        "category": "official_lab",
        "weight": 1.5,
        "text": (
            "Today we are open-sourcing Model Context Protocol (MCP) v2.0 with bi-directional streaming "
            "and enterprise access control. Over 1,500 integrations now supported natively in Claude Desktop."
        ),
        "hours_ago": 11,
        "likes": 9800,
        "retweets": 2400,
        "replies": 430,
        "urls": ["https://modelcontextprotocol.io"],
    },
    {
        "handle": "karpathy",
        "name": "Andrej Karpathy",
        "category": "pioneer_researcher",
        "weight": 1.5,
        "text": (
            "The transition from static pre-training scaling to dynamic inference-time compute (test-time search) "
            "is the biggest conceptual unlock since Transformers in 2017. "
            "Instead of 1 pass forward prediction, the model searches a tree of thoughts. "
            "Wrote a short walkthrough on how to build a minimal MCTS reasoning loop in PyTorch:"
        ),
        "hours_ago": 14,
        "likes": 28900,
        "retweets": 5900,
        "replies": 1150,
        "urls": ["https://github.com/karpathy/min-reasoning"],
    },
    {
        "handle": "DrJimFan",
        "name": "Jim Fan",
        "category": "pioneer_researcher",
        "weight": 1.3,
        "text": (
            "Project GR00T 2.0 announcement: NVIDIA Cosmos world foundation model now allows humanoid robots "
            "to generalize zero-shot to unseen household manipulation tasks using synthetic video pretraining. "
            "Physical AI is officially having its GPT-2 moment."
        ),
        "hours_ago": 16,
        "likes": 11200,
        "retweets": 2800,
        "replies": 620,
        "urls": ["https://blogs.nvidia.com/cosmos-robotics"],
    },
    {
        "handle": "ylecun",
        "name": "Yann LeCun",
        "category": "pioneer_researcher",
        "weight": 1.4,
        "text": (
            "Another paper showing that autoregressive LLMs cannot plan effectively without an explicit "
            "hierarchical world model. Autoregressive token generation accumulates errors exponentially. "
            "V-JEPA and objective-driven architectures remain the path to real AGI."
        ),
        "hours_ago": 19,
        "likes": 15400,
        "retweets": 3300,
        "replies": 1800,
        "urls": ["https://arxiv.org/abs/2412.xxxxx"],
    },
    {
        "handle": "rowancheung",
        "name": "Rowan Cheung",
        "category": "news_curator",
        "weight": 1.2,
        "text": (
            "Today in AI: 1) Google drops Gemini 2.5 Flash with reasoning 2) OpenAI rolls out Operator agents 3) "
            "Anthropic upgrades MCP 2.0 4) NVIDIA showcases Cosmos world foundation model for robotics. "
            "Insane 24 hours in AI."
        ),
        "hours_ago": 2,
        "likes": 8700,
        "retweets": 1900,
        "replies": 310,
        "urls": ["https://therundown.ai/daily-roundup"],
    },
    {
        "handle": "hwchase17",
        "name": "Harrison Chase",
        "category": "pioneer_researcher",
        "weight": 1.3,
        "text": (
            "LangGraph Cloud now supports human-in-the-loop approvals with sub-millisecond checkpoint resumption. "
            "Critical for autonomous agent pipelines that require human sign-off before executing financial or social actions."
        ),
        "hours_ago": 21,
        "likes": 4200,
        "retweets": 890,
        "replies": 180,
        "urls": ["https://langchain.com/blog/langgraph-checkpoints"],
    },
    # Noise/chatter tweet that should be filtered out by the prioritization agent
    {
        "handle": "sama",
        "name": "Sam Altman",
        "category": "pioneer_researcher",
        "weight": 1.5,
        "text": "great coffee in tokyo this morning ☕️",
        "hours_ago": 22,
        "likes": 9500,
        "retweets": 400,
        "replies": 750,
        "urls": [],
    }
]


class MockCollector(BaseCollector):
    """Returns realistic mock tweets for rapid local testing and offline execution."""

    async def collect(
        self,
        accounts: List[TwitterAccountConfig],
        max_per_account: int = 5,
        max_age_hours: int = 24,
    ) -> List[RawTweet]:
        """Generate high-signal mock tweets matching the account handles."""
        results: List[RawTweet] = []
        now = datetime.now(timezone.utc)

        # Map accounts by handle for quick lookup
        account_map = {acc.handle.lower(): acc for acc in accounts}

        for idx, item in enumerate(SAMPLE_TWEETS_DATA):
            handle_lower = item["handle"].lower()
            acc = account_map.get(handle_lower)
            if not acc and accounts:
                # If account is not in the configured list, skip or use item's metadata
                continue

            created_at = now - timedelta(hours=item["hours_ago"])
            if item["hours_ago"] > max_age_hours:
                continue

            tweet = RawTweet(
                id=f"mock_tw_{idx + 1000}",
                author_handle=item["handle"],
                author_name=item["name"],
                text=item["text"],
                created_at=created_at,
                likes=item["likes"],
                retweets=item["retweets"],
                replies=item["replies"],
                urls=item.get("urls", []),
                source_url=f"https://x.com/{item['handle']}/status/{idx + 1000}",
                author_weight=item.get("weight", 1.0),
                account_category=item.get("category", "pioneer_researcher"),
            )
            results.append(tweet)

        logger.info(f"Mock collector produced {len(results)} high-signal test tweets.")
        return results
