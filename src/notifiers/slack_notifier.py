"""Slack notification service using Incoming Webhooks."""

import logging
from typing import Optional
import httpx

from config.settings import settings
from src.models.news import NewsRankingResult

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Dispatches prioritized news updates to Slack channel via Incoming Webhook."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.SLACK_WEBHOOK_URL

    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.startswith("https://hooks.slack.com"))

    async def send_ranking_digest(self, result: NewsRankingResult) -> bool:
        """Send formatted Slack message matching the user's reference design."""
        if not self.is_configured():
            logger.debug("Slack webhook not configured.")
            return False

        slack_text = "*AI UPDATES — LAST 24 HOURS*\n\n"
        slack_text += f"_(Top {len(result.items)} prioritized updates out of {result.total_tweets_collected} collected)_\n\n"

        for idx, item in enumerate(result.items, start=1):
            marker = "⭐ " if item.id == (result.top_pick.id if result.top_pick else "") else "• "
            first_url = item.source_urls[0] if item.source_urls else ""

            slack_text += (
                f"{marker}*{item.title}* ({item.scores.total:.1f}/100)\n"
                f"_{item.summary}_\n"
                f"💡 Why it matters: {item.why_it_matters}\n"
                f"{first_url}\n\n"
            )

        payload = {"text": slack_text}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(self.webhook_url, json=payload)
                if resp.status_code == 200:
                    logger.info("Successfully sent digest to Slack.")
                    return True
                logger.error(f"Slack webhook error {resp.status_code}: {resp.text}")
                return False
            except Exception as e:
                logger.error(f"Failed to post to Slack: {e}")
                return False
