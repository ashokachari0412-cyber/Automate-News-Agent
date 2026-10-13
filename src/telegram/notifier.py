"""Telegram notification and interaction service for Human-in-the-Loop review & video generation."""

import html
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
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

    async def send_ranking_digest(self, result: NewsRankingResult, chat_id: Optional[str] = None) -> bool:
        """Step 1: Send clean news updates (without video hooks) with 'Generate Video' buttons."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid:
            logger.warning("Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID).")
            return False

        now_str = datetime.now().strftime("%d-%b-%y")
        header_msg = f"<b>News: {now_str}</b>"
        await self._send_message(header_msg, chat_id=cid)

        # Send individual clean news cards
        for idx, item in enumerate(result.items, start=1):
            msg, reply_markup = self._format_clean_news_item(item, rank=idx)
            await self._send_message(msg, reply_markup=reply_markup, chat_id=cid)

        return True

    def _format_clean_news_item(self, item: PrioritizedNewsItem, rank: int) -> tuple[str, Dict[str, Any]]:
        """Formats clean news card with accurate publication timing and word dictionary."""
        raw_title = item.title.strip()
        short_title = (raw_title[:30] + "...") if len(raw_title) > 30 else raw_title

        # Bullets with tasteful emoji icons
        bullet_emojis = ["⚡", "🚀", "💡"]
        bullets_formatted = "\n".join([
            f"{bullet_emojis[i % len(bullet_emojis)]} {html.escape(b)}"
            for i, b in enumerate(item.key_bullet_points[:3])
        ])

        # Source link formatting
        source_name = item.primary_source_handle or "Verified Source"
        if item.source_urls:
            first_url = item.source_urls[0]
            source_link = f'<a href="{first_url}">{html.escape(source_name)}</a>'
        else:
            source_link = html.escape(source_name)

        # Accurate publication timing
        if item.published_at:
            now_utc = datetime.now(timezone.utc)
            pub_dt = item.published_at if item.published_at.tzinfo else item.published_at.replace(tzinfo=timezone.utc)
            delta_seconds = max(0, (now_utc - pub_dt).total_seconds())
            delta_hours = delta_seconds / 3600.0

            if delta_hours < 1:
                mins = max(1, int(delta_seconds / 60))
                time_str = f"Published: {mins}m ago"
            elif delta_hours < 24:
                time_str = f"Published: {pub_dt.strftime('%H:%M UTC')} ({int(delta_hours)}h ago)"
            else:
                time_str = f"Published: {pub_dt.strftime('%d-%b-%Y')}"
        else:
            time_str = f"Published: {datetime.now().strftime('%d-%b-%Y')}"

        # In-place expandable vocabulary section (never leaves the chat page)
        vocab_section = ""
        if item.vocabulary:
            vocab_items = [f"• <b>{html.escape(k)}:</b> {html.escape(v)}" for k, v in item.vocabulary.items()]
            vocab_section = f"\n\n<blockquote expandable><b>📖 Word Meanings:</b>\n" + "\n".join(vocab_items) + "</blockquote>"

        type_name = item.category.upper() if getattr(item, "category", None) else "AI UPDATE"
        # Dynamic category icon
        type_icon = "🔥" if any(k in type_name for k in ["TOOL", "APP", "LAUNCH"]) else (
            "🧠" if any(k in type_name for k in ["MODEL", "FRONTIER", "LLM"]) else (
                "🤖" if any(k in type_name for k in ["AGENT", "WORKFLOW"]) else (
                    "🎨" if any(k in type_name for k in ["GENERATIVE", "DIFFUSION", "CREATIVE"]) else (
                        "💻" if any(k in type_name for k in ["HARDWARE", "CHIP", "COMPUTE"]) else "📌"
                    )
                )
            )
        )

        msg = (
            f"🏷️ <b>Type:</b> {type_icon} <b>{html.escape(type_name)}</b>\n"
            f"⚡ <b>U-{rank}: \"{html.escape(short_title)}\"</b>\n\n"
            f"📝 {html.escape(item.summary)}\n\n"
            f"🎯 <b>Key Takeaways:</b>\n"
            f"{bullets_formatted}\n\n"
            f"💡 <b>Why It Matters:</b>\n"
            f"{html.escape(item.why_it_matters)}"
            f"{vocab_section}\n\n"
            f"🔗 <b>Source:</b> {source_link} | 🕒 {time_str}"
        )

        inline_keyboard = [
            [
                {
                    "text": "🔍 Know more",
                    "callback_data": f"know_more:{item.id}"
                },
                {
                    "text": "🎬 Generate Vid",
                    "callback_data": f"gen_video:{item.id}"
                },
                {
                    "text": "🖼️ Post as Images",
                    "callback_data": f"post_images:{item.id}"
                }
            ],
            [
                {
                    "text": "📖 Word Meanings",
                    "callback_data": f"vocab:{item.id}"
                }
            ]
        ]

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
                        "reply_markup": json.dumps({"inline_keyboard": inline_keyboard}),
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

    async def send_photo_album(
        self,
        photo_paths: List[Path],
        caption: str = "",
        chat_id: Optional[str] = None,
    ) -> bool:
        """Uploads multiple 3:4 Instagram carousel photos as an album (media group) to Telegram."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid or not photo_paths:
            return False

        async with httpx.AsyncClient(timeout=120.0) as client:
            open_files = []
            try:
                media_list = []
                files = {}
                for idx, p in enumerate(photo_paths):
                    var_name = f"photo_{idx}"
                    entry: Dict[str, Any] = {
                        "type": "photo",
                        "media": f"attach://{var_name}",
                    }
                    if idx == 0 and caption:
                        entry["caption"] = caption
                        entry["parse_mode"] = "HTML"
                    media_list.append(entry)
                    f_handle = open(p, "rb")
                    open_files.append(f_handle)
                    files[var_name] = (p.name, f_handle, "image/png")

                data = {
                    "chat_id": cid,
                    "media": json.dumps(media_list),
                }

                resp = await client.post(f"{self.base_url}/sendMediaGroup", data=data, files=files)
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"Failed to send photo album: {e}")
                return False
            finally:
                for fh in open_files:
                    try:
                        fh.close()
                    except Exception:
                        pass

    async def send_photo_file(
        self,
        photo_path: Path,
        caption: str = "",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """Upload a single photo file to Telegram chat."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid or not photo_path.exists():
            return False

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                with open(photo_path, "rb") as pf:
                    files = {"photo": (photo_path.name, pf, "image/png")}
                    data: Dict[str, Any] = {
                        "chat_id": cid,
                        "caption": caption,
                        "parse_mode": "HTML",
                    }
                    if reply_markup:
                        data["reply_markup"] = json.dumps(reply_markup)
                    resp = await client.post(f"{self.base_url}/sendPhoto", data=data, files=files)
                    return resp.status_code == 200
            except Exception as e:
                logger.error(f"Error sending photo: {e}")
                return False

    async def edit_reply_markup(
        self,
        message_id: int,
        reply_markup: Dict[str, Any],
        chat_id: Optional[str] = None,
    ) -> bool:
        """Dynamically edits inline keyboard (for toggling checkboxes in place)."""
        cid = chat_id or self.chat_id
        if not self.base_url or not cid:
            return False

        payload = {
            "chat_id": cid,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/editMessageReplyMarkup", json=payload)
                return resp.status_code == 200
            except Exception as e:
                logger.debug(f"Failed to edit reply markup: {e}")
                return False
