"""Telegram notification and interaction service for Human-in-the-Loop review & video generation."""

import html
import logging
import os
from typing import Any, Dict, List, Optional
import httpx

from config.settings import settings
from src.models.news import NewsRankingResult, PrioritizedNewsItem
from src.models.script import VideoBlueprint

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Dispatches clean news updates, 35-40s blueprints, and final video delivery to Telegram."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        raw_token = bot_token or settings.TELEGRAM_BOT_TOKEN or ""
        raw_chat = str(chat_id or settings.TELEGRAM_CHAT_ID or "")
        self.bot_token = raw_token.strip("\"'")
        self.chat_id = raw_chat.strip("\"'")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else ""

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send_ranking_digest(self, result: NewsRankingResult) -> bool:
        """Step 1: Send clean news updates (without video hooks) with 'Generate Video' buttons."""
        if not self.is_configured():
            logger.warning("Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID).")
            return False

        header_msg = (
            "🤖 <b>AI CONTENT AGENT: DAILY INTELLIGENCE DIGEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Strict 24h Window:</b> {result.total_tweets_collected} posts scanned\n"
            f"🎯 <b>Action:</b> Review latest stories below and tap <b>[🎬 Generate Video]</b> to create a 35-40s short."
        )
        await self._send_message(header_msg)

        # Send individual clean news cards (NO video hook here, just clean informative news)
        for idx, item in enumerate(result.items, start=1):
            msg, reply_markup = self._format_clean_news_item(item, rank=idx)
            await self._send_message(msg, reply_markup=reply_markup)

        return True

    def _format_clean_news_item(self, item: PrioritizedNewsItem, rank: int) -> tuple[str, Dict[str, Any]]:
        """Formats a clean, informative news update without the video hook."""
        bullets_formatted = "\n".join([f"  • {html.escape(b)}" for b in item.key_bullet_points[:3]])

        msg = (
            f"📰 <b>#{rank} | {html.escape(item.title)}</b>\n"
            f"<b>Source:</b> @{item.primary_source_handle} | <b>Category:</b> #{item.category.replace(' ', '')}\n\n"
            f"📝 <b>What Happened:</b>\n{html.escape(item.summary)}\n\n"
            f"💡 <b>Key Takeaways:</b>\n{bullets_formatted}\n\n"
            f"🎯 <b>Why It Matters:</b>\n{html.escape(item.why_it_matters)}"
        )

        inline_keyboard = [
            [
                {
                    "text": "🎬 Generate Video for This",
                    "callback_data": f"gen_video:{item.id}"
                }
            ]
        ]

        if item.source_urls:
            inline_keyboard.append([
                {
                    "text": "🔗 View Original Source",
                    "url": item.source_urls[0]
                }
            ])

        return msg, {"inline_keyboard": inline_keyboard}

    async def send_video_blueprint(self, blueprint: VideoBlueprint, chat_id: Optional[str] = None) -> Optional[int]:
        """Step 2: Send 35-40s clip breakdown, hook, and avatar selection."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid:
            return None

        scenes_text = ""
        for s in blueprint.scenes:
            scenes_text += (
                f"\n• <b>Scene {s.scene_number} ({s.timestamp}):</b>\n"
                f"  <i>Visual:</i> {html.escape(s.visual_description[:65])}...\n"
                f"  <i>Spoken:</i> \"{html.escape(s.spoken_script)}\"\n"
            )

        msg = (
            f"🎬 <b>{blueprint.estimated_duration_seconds}s VIDEO BLUEPRINT:</b>\n"
            f"📌 <b>Topic:</b> {html.escape(blueprint.title)}\n"
            f"⏱ <b>Target Duration:</b> ~{blueprint.estimated_duration_seconds} seconds (9:16 Vertical)\n\n"
            f"🎣 <b>Hook:</b>\n<i>\"{html.escape(blueprint.hook)}\"</i>\n\n"
            f"🎞 <b>Clip & Scene Breakdown:</b>{scenes_text}\n"
            f"👤 <b>Active Avatar:</b> <code>{html.escape(blueprint.avatar_profile)}</code>"
        )

        inline_keyboard = [
            [
                {
                    "text": "🚀 Render Final Video (Google AI)",
                    "callback_data": f"render_final:{blueprint.news_id}"
                }
            ],
            [
                {
                    "text": "👤 Switch Avatar: Casual Creator",
                    "callback_data": f"avatar:{blueprint.news_id}:casual"
                },
                {
                    "text": "👤 Executive",
                    "callback_data": f"avatar:{blueprint.news_id}:executive"
                }
            ],
            [
                {
                    "text": "✏️ Edit Script / Hook",
                    "callback_data": f"edit_script:{blueprint.news_id}"
                }
            ]
        ]

        return await self._send_message_and_get_id(msg, {"inline_keyboard": inline_keyboard}, chat_id=cid)

    async def update_progress(self, message_id: int, text: str, chat_id: Optional[str] = None):
        """Update live progress on an existing Telegram message."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid or not message_id:
            return

        payload = {
            "chat_id": cid,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(f"{self.base_url}/editMessageText", json=payload)
            except Exception as e:
                logger.debug(f"Progress update warning: {e}")

    async def send_video_file(
        self,
        video_path: str,
        caption: str = "",
        chat_id: Optional[str] = None,
    ) -> bool:
        """Step 3: Upload finished MP4 video file to Telegram chat."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid or not os.path.exists(video_path):
            logger.error(f"Cannot upload video: missing credentials or file not found ({video_path})")
            return False

        logger.info(f"Uploading video {video_path} to Telegram chat {cid}...")

        inline_keyboard = [
            [
                {
                    "text": "👍 Approve Video (Proceed to Publish)",
                    "callback_data": "video_approved"
                },
                {
                    "text": "🔄 Re-generate",
                    "callback_data": "video_regen"
                }
            ]
        ]

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                with open(video_path, "rb") as video_file:
                    files = {"video": (os.path.basename(video_path), video_file, "video/mp4")}
                    data = {
                        "chat_id": cid,
                        "caption": caption,
                        "parse_mode": "HTML",
                        "supports_streaming": "true",
                        "reply_markup": httpx._utils.json_dumps({"inline_keyboard": inline_keyboard}),
                    }
                    resp = await client.post(f"{self.base_url}/sendVideo", data=data, files=files)
                    if resp.status_code == 200:
                        logger.info("Video delivered successfully to Telegram!")
                        return True
                    logger.error(f"Telegram sendVideo error {resp.status_code}: {resp.text}")
                    return False
            except Exception as e:
                logger.error(f"Failed to upload video to Telegram: {e}")
                return False

    async def _send_message(self, text: str, reply_markup: Optional[Dict[str, Any]] = None, chat_id: Optional[str] = None) -> bool:
        """Internal helper to dispatch Telegram message."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid:
            return False

        payload: Dict[str, Any] = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                if resp.status_code != 200 and "parse_mode" in payload:
                    # Fallback to plain text if HTML parsing encounters any unescaped tags
                    plain_payload = dict(payload)
                    plain_payload.pop("parse_mode", None)
                    resp = await client.post(f"{self.base_url}/sendMessage", json=plain_payload)
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")
                return False

    async def _send_message_and_get_id(self, text: str, reply_markup: Optional[Dict[str, Any]] = None, chat_id: Optional[str] = None) -> Optional[int]:
        """Internal helper returning the newly sent message_id for live progress editing."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid:
            return None

        payload: Dict[str, Any] = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result", {}).get("message_id")
            except Exception as e:
                logger.error(f"Error sending message: {e}")
        return None
