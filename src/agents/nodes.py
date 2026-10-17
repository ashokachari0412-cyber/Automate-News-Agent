"""LangGraph node implementations for the AI Research Agent."""

import json
import logging
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from config.settings import TwitterAccountConfig, settings
from src import progress
from src.collectors.base import BaseCollector
from src.collectors.factory import get_collector
from src.llm.client import LLMClient
from src.models.news import NewsRankingResult, NewsScoreBreakdown, PrioritizedNewsItem
from src.models.tweet import RawTweet, TweetCluster
from src.agents.prompts import NEWS_EVALUATION_PROMPT, SYSTEM_RESEARCH_PROMPT

logger = logging.getLogger(__name__)


class ResearchState(TypedDict, total=False):
    """LangGraph state schema for AI news research and prioritization."""
    accounts: List[TwitterAccountConfig]
    raw_tweets: List[RawTweet]
    filtered_tweets: List[RawTweet]
    clusters: List[TweetCluster]
    ranked_news: List[PrioritizedNewsItem]
    top_pick: Optional[PrioritizedNewsItem]
    result: Optional[NewsRankingResult]
    error: Optional[str]


class ResearchNodes:
    """Encapsulates node execution logic."""

    def __init__(self, collector: Optional[BaseCollector] = None, llm_client: Optional[LLMClient] = None):
        self.collector = collector or get_collector(settings)
        self.llm_client = llm_client or LLMClient(settings)

    async def collect_tweets_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 1: Fetches latest posts from configured trusted Twitter accounts."""
        accounts = state.get("accounts") or settings.get_trusted_accounts()
        logger.info(f"Running collect_tweets_node across {len(accounts)} trusted accounts.")
        progress.report(f"Collecting the latest posts from {len(accounts)} trusted AI sources...")

        try:
            tweets = await self.collector.collect(
                accounts=accounts,
                max_per_account=settings.TWEETS_PER_ACCOUNT,
                max_age_hours=settings.MAX_TWEET_AGE_HOURS,
            )
            logger.info(f"Collected {len(tweets)} raw tweets.")
            return {"raw_tweets": tweets, "accounts": accounts}
        except Exception as e:
            logger.error(f"Error in collect_tweets_node: {e}")
            return {"raw_tweets": [], "error": str(e)}

    async def filter_noise_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 2: Filters out personal chit-chat, short spam, and irrelevant posts."""
        raw_tweets = state.get("raw_tweets", [])
        filtered: List[RawTweet] = []
        progress.report(f"Found {len(raw_tweets)} posts, keeping only real news from the last 24 hours...")

        # Common noise patterns to discard
        noise_keywords = ["coffee", "lunch", "dinner", "flight", "weather", "gm", "gn", "happy birthday"]

        # Strict 24-hour cutoff
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.MAX_TWEET_AGE_HOURS)

        for tweet in raw_tweets:
            # Enforce 24h window
            if tweet.created_at and tweet.created_at < cutoff:
                continue

            text_lower = tweet.text.lower().strip()
            # Discard very short tweets without links
            if len(text_lower) < 25 and not tweet.urls:
                continue

            # Discard trivial personal status updates
            if any(text_lower.startswith(k) or text_lower == k for k in noise_keywords):
                continue

            filtered.append(tweet)

        logger.info(f"Noise filter: Kept {len(filtered)} high-signal candidates from {len(raw_tweets)} raw tweets.")
        return {"filtered_tweets": filtered}

    async def cluster_tweets_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 3: Groups tweets covering related topics/events."""
        filtered_tweets = state.get("filtered_tweets", [])
        clusters: List[TweetCluster] = []
        progress.report(f"Grouping {len(filtered_tweets)} posts that talk about the same story...")

        # Simple semantic clustering by URL overlap and key phrase matching
        clusters_by_key: Dict[str, List[RawTweet]] = {}

        for tweet in filtered_tweets:
            assigned = False
            # Check URL overlap
            if tweet.urls:
                for url in tweet.urls:
                    domain_key = url.split("?")[0]
                    if domain_key in clusters_by_key:
                        clusters_by_key[domain_key].append(tweet)
                        assigned = True
                        break
                if assigned:
                    continue

            # Check core entity keywords
            text_upper = tweet.text.upper()
            core_entities = ["GEMINI", "VEO", "OPERATOR", "CLAUDE", "MCP", "COSMOS", "LLAMA", "MISTRAL", "DEEPSEEK"]
            for entity in core_entities:
                if entity in text_upper:
                    clusters_by_key.setdefault(entity, []).append(tweet)
                    assigned = True
                    break

            if not assigned:
                clusters_by_key.setdefault(tweet.id, []).append(tweet)

        # Convert to TweetCluster models
        for key, tw_list in clusters_by_key.items():
            primary = max(tw_list, key=lambda t: t.author_weight * (1 + (t.likes / 1000)))
            cluster = TweetCluster(
                cluster_id=f"cluster_{key[:20]}",
                headline_hint=primary.text[:80] + "...",
                tweets=tw_list,
                primary_handle=primary.author_handle,
                combined_weight=max(t.author_weight for t in tw_list),
            )
            clusters.append(cluster)

        logger.info(f"Clustered into {len(clusters)} distinct news candidates.")
        return {"clusters": clusters}

    async def prioritize_and_rank_node(self, state: ResearchState) -> Dict[str, Any]:
        """Node 4: Uses Gemini / Groq to score, prioritize, and structure news items."""
        clusters = state.get("clusters", [])
        filtered_tweets = state.get("filtered_tweets", [])
        raw_tweets = state.get("raw_tweets", [])
        progress.report(f"Gemini is scoring {len(clusters)} stories and picking the most important ones...")

        # Prepare summary of candidates for LLM prompt
        candidates_payload = []
        for cluster in clusters:
            primary = cluster.tweets[0]
            candidates_payload.append({
                "cluster_id": cluster.cluster_id,
                "author": f"@{primary.author_handle} ({primary.author_name})",
                "category": primary.account_category,
                "text": primary.text,
                "likes": primary.likes,
                "retweets": primary.retweets,
                "urls": primary.urls,
                "corroborating_tweets_count": len(cluster.tweets),
            })

        prompt_input = NEWS_EVALUATION_PROMPT.format(
            tweets_json=json.dumps(candidates_payload[:15], indent=2)
        )

        llm_response = await self.llm_client.generate_json(
            prompt=prompt_input,
            system_prompt=SYSTEM_RESEARCH_PROMPT,
        )

        raw_items = llm_response.get("items", [])
        ranked_items: List[PrioritizedNewsItem] = []

        # Find cluster lookup map
        cluster_map = {c.cluster_id: c for c in clusters}

        for idx, item_data in enumerate(raw_items):
            try:
                scores_data = item_data.get("scores", {})
                scores = NewsScoreBreakdown(
                    impact=float(scores_data.get("impact", 20.0)),
                    novelty=float(scores_data.get("novelty", 20.0)),
                    credibility=float(scores_data.get("credibility", 20.0)),
                    video_adaptability=float(scores_data.get("video_adaptability", 20.0)),
                    total=float(scores_data.get("total", 80.0)),
                )

                # Determine priority tier
                if scores.total >= settings.HIGH_PRIORITY_THRESHOLD:
                    tier = "HIGH"
                elif scores.total >= settings.MEDIUM_PRIORITY_THRESHOLD:
                    tier = "MEDIUM"
                else:
                    tier = "LOW"

                # Link sources
                candidate_id = item_data.get("candidate_id", "")
                associated_cluster = cluster_map.get(candidate_id)
                source_urls = []
                primary_handle = ""
                tweet_ids = []

                if associated_cluster:
                    primary_handle = associated_cluster.primary_handle
                    for tw in associated_cluster.tweets:
                        source_urls.extend(tw.urls)
                        if tw.source_url:
                            source_urls.append(tw.source_url)
                        tweet_ids.append(tw.id)
                elif idx < len(clusters):
                    tw = clusters[idx].tweets[0]
                    primary_handle = tw.author_handle
                    source_urls.extend(tw.urls)
                    if tw.source_url:
                        source_urls.append(tw.source_url)
                    tweet_ids.append(tw.id)

                # Get publication timestamp from source tweet/feed item
                published_at = None
                if associated_cluster and associated_cluster.tweets:
                    published_at = associated_cluster.tweets[0].created_at
                elif idx < len(clusters) and clusters[idx].tweets:
                    published_at = clusters[idx].tweets[0].created_at

                raw_vocab = item_data.get("vocabulary", {})
                vocab_dict = {str(k): str(v) for k, v in raw_vocab.items()} if isinstance(raw_vocab, dict) else {}

                news_item = PrioritizedNewsItem(
                    id=f"news_{idx + 1}",
                    title=item_data.get("title", "AI Development"),
                    hook=item_data.get("hook", ""),
                    summary=item_data.get("summary", ""),
                    key_bullet_points=item_data.get("key_bullet_points", []),
                    category=item_data.get("category", "Frontier Model"),
                    scores=scores,
                    priority_tier=tier,
                    why_it_matters=item_data.get("why_it_matters", ""),
                    suggested_video_angle=item_data.get("suggested_video_angle", ""),
                    primary_source_handle=primary_handle,
                    source_urls=list(set(source_urls))[:4],
                    source_tweet_ids=tweet_ids,
                    published_at=published_at,
                    vocabulary=vocab_dict,
                    image_prompt=item_data.get("image_prompt", ""),
                )
                ranked_items.append(news_item)
            except Exception as e:
                logger.warning(f"Error parsing news item {idx}: {e}")

        # Sort descending by total score
        ranked_items.sort(key=lambda x: x.scores.total, reverse=True)

        top_pick = ranked_items[0] if ranked_items else None

        result = NewsRankingResult(
            items=ranked_items[:settings.TOP_NEWS_LIMIT],
            total_tweets_collected=len(raw_tweets),
            total_candidates_analyzed=len(filtered_tweets),
            top_pick=top_pick,
        )

        logger.info(f"Prioritization complete. Produced {len(ranked_items)} ranked news items.")
        return {
            "ranked_news": ranked_items,
            "top_pick": top_pick,
            "result": result,
        }
