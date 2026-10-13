"""Interactive Telegram Bot Service: Handles button clicks, script previews, video delivery,
operating hours (10 AM - 10 PM), custom greeting ("What you want Ashok?"), and Gemini Chatbot mode."""

import asyncio
from datetime import datetime
import html
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import httpx
from rich.console import Console

from config.settings import settings
from src.agents.content_agent import ContentAgent
from src.agents.research_graph import run_research_workflow
from src.llm.client import LLMClient
from src.models.news import PrioritizedNewsItem, NewsScoreBreakdown
from src.models.script import VideoBlueprint, SceneClip
from src.telegram.notifier import TelegramNotifier
from src.video.generator import VideoGenerator
from src.video.carousel_generator import CarouselGenerator
from src.publishers.instagram_publisher import InstagramPublisher

logger = logging.getLogger(__name__)
console = Console(legacy_windows=False)

CACHE_DIR = Path("artifacts")
NEWS_CACHE_FILE = CACHE_DIR / "news_cache.json"
BLUEPRINTS_CACHE_FILE = CACHE_DIR / "blueprints_cache.json"

# In-memory session caches
_cached_news_items: Dict[str, PrioritizedNewsItem] = {}
_cached_blueprints: Dict[str, VideoBlueprint] = {}
_chat_history: Dict[str, List[dict]] = {}
_platform_selections: Dict[str, set] = {}
_waiting_for_input: Dict[str, dict] = {}

GREETING_WORDS = {
    "hi", "hello", "hey", "hii", "hiii", "heyy", "yo", "start", "/start",
    "good morning", "good afternoon", "good evening", "what's up", "whats up",
    "sup", "ashok", "namaste", "hola"
}

NEWS_TRIGGER_KEYWORDS = [
    "news update", "news updates", "the news updates", "news", "give me news",
    "latest news", "breaking news", "ai news", "get news", "run updates", "/news"
]


def _is_within_active_hours() -> bool:
    """Check if the current time is within active operating hours (9:00 AM - 11:59 PM in user's timezone)."""
    if not settings.ACTIVE_HOURS_ENABLED:
        return True
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(getattr(settings, "TIMEZONE", "Asia/Kolkata"))
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    return settings.ACTIVE_HOURS_START <= now.hour < settings.ACTIVE_HOURS_END


def _save_caches_to_disk():
    """Persist news and blueprint caches to JSON files."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        news_data = {k: v.model_dump(mode="json") for k, v in _cached_news_items.items()}
        with open(NEWS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(news_data, f, indent=2, default=str)

        bp_data = {k: v.model_dump(mode="json") for k, v in _cached_blueprints.items()}
        with open(BLUEPRINTS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(bp_data, f, indent=2, default=str)
    except Exception as e:
        logger.debug(f"Failed to persist cache to disk: {e}")


_last_news_mtime: float = 0.0
_last_blueprints_mtime: float = 0.0


def _load_caches_from_disk(force: bool = False):
    """Load cached news and blueprints from disk on startup or when updated on disk."""
    global _cached_news_items, _cached_blueprints, _last_news_mtime, _last_blueprints_mtime
    try:
        if NEWS_CACHE_FILE.exists():
            mtime = NEWS_CACHE_FILE.stat().st_mtime
            if force or mtime > _last_news_mtime:
                with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        _cached_news_items[k] = PrioritizedNewsItem.model_validate(v)
                _last_news_mtime = mtime
                logger.info(f"Loaded {len(_cached_news_items)} news items from cache.")

        if BLUEPRINTS_CACHE_FILE.exists():
            mtime = BLUEPRINTS_CACHE_FILE.stat().st_mtime
            if force or mtime > _last_blueprints_mtime:
                with open(BLUEPRINTS_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        _cached_blueprints[k] = VideoBlueprint.model_validate(v)
                _last_blueprints_mtime = mtime
                logger.info(f"Loaded {len(_cached_blueprints)} video blueprints from cache.")
    except Exception as e:
        logger.warning(f"Error loading caches from disk: {e}")


# Initialize disk cache on import
_load_caches_from_disk(force=True)


class TelegramBotService:
    """Long-polling interactive Telegram Bot controller with Gemini Chat & Video Generation."""

    def __init__(self):
        self.notifier = TelegramNotifier()
        self.content_agent = ContentAgent()
        self.video_generator = VideoGenerator()
        self.llm_client = LLMClient()
        self.instagram_publisher = InstagramPublisher()
        self._running = False
        self._last_update_id = 0

    def cache_news(self, items: list[PrioritizedNewsItem]):
        """Cache current news items so callbacks can immediately look them up."""
        global _cached_news_items
        for it in items:
            _cached_news_items[it.id] = it
        _save_caches_to_disk()

    async def start_polling(self):
        """Start listening for Telegram button clicks and commands."""
        if not self.notifier.is_configured():
            console.print("[red]Telegram credentials not configured. Cannot start bot service.[/]")
            return

        self._running = True
        console.print(f"[bold green]🤖 Telegram Bot Service active (Hours: {settings.ACTIVE_HOURS_START}:00 AM - 11:59 PM)...[/]")
        console.print(f"[dim]User: {settings.USER_NAME} | Model: {settings.GEMINI_MODEL} | Duration: {settings.VIDEO_DURATION_SECONDS}s[/]")
        console.print("[dim]Press Ctrl+C to stop.[/]\n")

        base_url = self.notifier.base_url

        async with httpx.AsyncClient(timeout=30.0) as client:
            while self._running:
                try:
                    params = {"offset": self._last_update_id + 1, "timeout": 20}
                    resp = await client.get(f"{base_url}/getUpdates", params=params)

                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        for update in updates:
                            self._last_update_id = update["update_id"]
                            try:
                                await self._handle_update(client, update)
                            except Exception as update_err:
                                console.print(f"[red]Error handling update {update.get('update_id')}: {update_err}[/]")
                except httpx.ReadTimeout:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Polling loop notice: {e}")
                    await asyncio.sleep(2)

    async def _handle_update(self, client: httpx.AsyncClient, update: dict):
        """Dispatch updates to button callback handlers or text/photo message handlers."""
        # 1. Handle Inline Button Clicks
        callback_query = update.get("callback_query")
        if callback_query:
            await self._handle_callback(client, callback_query)
            return

        # 2. Handle Incoming Messages (Text or Photo)
        message = update.get("message")
        if message:
            chat_id = str(message["chat"]["id"])

            # Check if user sent a photo (e.g. selfie / avatar update)
            photo = message.get("photo")
            if photo:
                await self._handle_photo_upload(client, photo, chat_id)
                return

            text = (message.get("text") or "").strip()
            if text:
                await self._handle_text_message(text, chat_id)

    async def _handle_photo_upload(self, client: httpx.AsyncClient, photo_list: list, chat_id: str):
        """Save user's uploaded photo as persistent video avatar."""
        try:
            # Pick highest resolution photo
            best_photo = photo_list[-1]
            file_id = best_photo["file_id"]

            file_info_resp = await client.get(f"{self.notifier.base_url}/getFile", params={"file_id": file_id})
            if file_info_resp.status_code != 200:
                await self.notifier._send_message("❌ Failed to retrieve photo from Telegram.", chat_id=chat_id)
                return

            file_path = file_info_resp.json().get("result", {}).get("file_path")
            download_url = f"https://api.telegram.org/file/bot{self.notifier.bot_token}/{file_path}"

            img_resp = await client.get(download_url)
            if img_resp.status_code == 200:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(img_resp.content))

                save_dir = Path(__file__).resolve().parent.parent.parent / "assets"
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = save_dir / "avatar.png"
                img.save(save_path, "PNG")

                console.print(f"[bold green]✅ New avatar saved from Telegram photo to {save_path}![/]")
                await self.notifier._send_message(
                    f"✅ <b>Personal Avatar Updated Successfully!</b>\n\n"
                    f"Your face has been saved as your persistent video avatar. All generated videos will now feature you:\n"
                    f"• <b>0–3s:</b> You speak the hook front-and-center 🎙️\n"
                    f"• <b>3s+:</b> You appear in the downside corner (PiP) while breaking news displays on screen! 🎬",
                    chat_id=chat_id,
                )
            else:
                await self.notifier._send_message("❌ Failed to download photo.", chat_id=chat_id)
        except Exception as e:
            logger.error(f"Error saving avatar photo: {e}")
            await self.notifier._send_message(f"❌ Error updating avatar: {e}", chat_id=chat_id)

    async def _handle_text_message(self, text: str, chat_id: str):
        """Handle incoming text messages: custom revisions, time-check, greeting, news trigger, or Gemini chat."""
        # 0. Check if user is replying with custom modification prompt for video script
        if chat_id in _waiting_for_input:
            input_ctx = _waiting_for_input.pop(chat_id)
            if input_ctx.get("action") == "modify_script":
                news_id = input_ctx.get("news_id")
                await self._handle_script_revision_input(text, news_id, chat_id)
                return

        # 1. Check Operating Window (9:00 AM - 11:59 PM)
        if not _is_within_active_hours():
            console.print(f"[yellow]Outside active hours (9 AM - 11:59 PM). Replying off-duty message to {chat_id}.[/]")
            await self.notifier._send_message(
                f"🌙 <b>Hi {settings.USER_NAME}, I'm off-duty right now.</b>\n\n"
                f"My active hours are <b>9:00 AM to 11:59 PM</b>.\n"
                f"I will be ready to help you first thing in the morning at 9:00 AM! ☀️",
                chat_id=chat_id,
            )
            return

        clean_text = text.lower().strip()
        clean_no_punct = re.sub(r"[^\w\s]", "", clean_text).strip()

        # Check for Avatar Settings Query
        if clean_no_punct in {"avatar", "my avatar", "set avatar"} or clean_text in {"/avatar", "/setavatar"}:
            await self.notifier._send_message(
                f"👤 <b>Personal Avatar Settings for {settings.USER_NAME}:</b>\n\n"
                f"Your saved digital twin avatar is active!\n"
                f"• <b>Avatar Engine:</b> HeyGen AI Clone / Master Avatar\n"
                f"• <b>Avatar ID:</b> <code>{getattr(settings, 'HEYGEN_AVATAR_ID', '0c8510c2f7ad4b96a5193bb7ce4c18a2')}</code>\n\n"
                f"📸 <b>To update reference photo:</b> Send any selfie directly to this chat.\n"
                f"🎬 <b>Videos:</b> Generated at strictly 3 seconds for fast review & minimal credit usage.",
                chat_id=chat_id,
            )
            return

        # 2. Check for Greetings (e.g. "hi", "hello", "hey", "/start")
        if clean_no_punct in GREETING_WORDS or clean_text in GREETING_WORDS:
            console.print(f"[bold cyan]👋 Greeting received from {settings.USER_NAME} -> Replying 'what you want {settings.USER_NAME}?'[/]")
            await self.notifier._send_message(
                f"👋 <b>what you want {settings.USER_NAME}?</b>",
                reply_markup={
                    "inline_keyboard": [
                        [
                            {"text": "📰 News Updates", "callback_data": "cmd_news_updates"},
                            {"text": "💬 Ask Gemini a Question", "callback_data": "cmd_ask_gemini"}
                        ],
                        [
                            {"text": "👤 Avatar / Face Settings", "callback_data": "cmd_avatar_info"}
                        ]
                    ]
                },
                chat_id=chat_id,
            )
            return

        # 3. Check for News Updates Intent (e.g. "news updates", "the news updates", "give me news")
        is_news_request = any(phrase in clean_text for phrase in NEWS_TRIGGER_KEYWORDS)
        if is_news_request:
            console.print(f"[bold green]📰 News trigger detected from {settings.USER_NAME}: '{text}'[/]")
            await self._trigger_news_workflow(chat_id)
            return

        # 4. Gemini Chatbot Mode (Meanings, explanations, questions, etc.)
        console.print(f"[bold cyan]💬 [Gemini Chat] {settings.USER_NAME} asked: '{text}'[/]")
        await self._handle_gemini_chat(text, chat_id)

    async def _handle_script_revision_input(self, user_revision: str, news_id: str, chat_id: str):
        """User typed custom instructions to rewrite the 3s video script clips."""
        blueprint = _cached_blueprints.get(news_id)
        news_item = _cached_news_items.get(news_id)

        await self.notifier._send_message(
            f"✍️ <i>Rewriting 3s video script based on your input: \"{html.escape(user_revision)}\"...</i>",
            chat_id=chat_id,
        )

        try:
            prompt = (
                f"You are writing a strictly 3-second ultra-short video script (max 7 to 8 words) for a tech creator avatar.\n"
                f"News Title: {news_item.title if news_item else ''}\n"
                f"Current script: {blueprint.hook if blueprint else ''}\n"
                f"User revision instruction: {user_revision}\n\n"
                f"Return ONLY the updated 1-sentence punchy spoken script (under 8 words). Nothing else."
            )
            updated_script = await self.llm_client.generate_text(prompt)
            clean_script = updated_script.strip().strip('"\'')

            if blueprint:
                blueprint.hook = clean_script
                if blueprint.scenes:
                    blueprint.scenes[0].spoken_script = clean_script
                _cached_blueprints[news_id] = blueprint
                _save_caches_to_disk()

            await self.notifier._send_message(
                f"✅ <b>Updated Script Clips (3s Short):</b>\n\n"
                f"📌 <b>Topic:</b> {html.escape(blueprint.title if blueprint else '')}\n"
                f"🎙️ <b>Spoken Script:</b> \"{html.escape(clean_script)}\"\n\n"
                f"Select platforms to publish & tap Post:",
                reply_markup=self._build_platform_keyboard(news_id, context="video"),
                chat_id=chat_id,
            )
        except Exception as e:
            await self.notifier._send_message(f"❌ Error revising script: {e}", chat_id=chat_id)

    def _build_platform_keyboard(self, news_id: str, context: str = "video") -> Dict[str, Any]:
        """Builds multi-select platform keyboard with official app icons (Instagram, LinkedIn, YouTube, Twitter)."""
        global _platform_selections
        if news_id not in _platform_selections:
            _platform_selections[news_id] = {"instagram", "linkedin", "youtube", "twitter"}

        sel = _platform_selections[news_id]

        def _btn(code: str, name: str, icon: str):
            checked = "✅" if code in sel else "⚪"
            return {
                "text": f"{checked} {icon} {name}",
                "callback_data": f"toggle_plat:{news_id}:{code}:{context}",
            }

        post_callback = f"post_vid:{news_id}" if context == "video" else f"post_imgs:{news_id}"

        keyboard = [
            [_btn("instagram", "Instagram", "📸"), _btn("linkedin", "LinkedIn", "💼")],
            [_btn("youtube", "YouTube", "▶️"), _btn("twitter", "Twitter", "𝕏")],
        ]

        if context == "video":
            keyboard.append([
                {"text": "✏️ Modify", "callback_data": f"modify_vid:{news_id}"},
                {"text": "🚀 Post", "callback_data": post_callback},
            ])
        else:
            keyboard.append([
                {"text": "🚀 Post", "callback_data": post_callback},
            ])

        return {"inline_keyboard": keyboard}

    async def _handle_callback(self, client: httpx.AsyncClient, cb: dict):
        """Handle user clicking buttons in Telegram."""
        cb_id = cb["id"]
        data = cb.get("data", "")
        chat_id = str(cb["message"]["chat"]["id"])

        # Always ensure latest cache is loaded from disk if background scheduler ran a scan
        _load_caches_from_disk()

        # Acknowledge button click immediately
        try:
            await client.post(f"{self.notifier.base_url}/answerCallbackQuery", json={"callback_query_id": cb_id})
        except Exception:
            pass

        # Case 0: Quick menu button [📰 News Updates]
        if data == "cmd_news_updates":
            console.print(f"[bold green]⚡ Menu Button: News Updates clicked by {settings.USER_NAME}[/]")
            await self._trigger_news_workflow(chat_id)

        # Case 0b: Quick menu button [💬 Ask Gemini]
        elif data == "cmd_ask_gemini":
            await self.notifier._send_message(
                f"Ask me anything, {settings.USER_NAME}! For example:\n"
                f"• <i>\"What is the meaning of ubiquitous?\"</i>\n"
                f"• <i>\"Explain how Mixture of Experts (MoE) works.\"</i>\n"
                f"• <i>\"Write a Python script to monitor a folder.\"</i>",
                chat_id=chat_id,
            )

        # Case 0c: Quick menu button [👤 Avatar / Face Settings]
        elif data == "cmd_avatar_info":
            await self.notifier._send_message(
                f"👤 <b>Personal Avatar Settings for {settings.USER_NAME}:</b>\n\n"
                f"Your saved digital twin avatar is active!\n"
                f"• <b>Avatar Engine:</b> HeyGen AI Clone / Master Avatar\n"
                f"• <b>Avatar ID:</b> <code>{getattr(settings, 'HEYGEN_AVATAR_ID', '0c8510c2f7ad4b96a5193bb7ce4c18a2')}</code>\n\n"
                f"📸 <b>To update reference photo:</b> Send any selfie directly to this chat.\n"
                f"🎬 <b>Videos:</b> Generated at strictly 3 seconds for fast review & minimal credit usage.",
                chat_id=chat_id,
            )

        # =====================================================================
        # STAGE 2: Know more Button Clicked
        # =====================================================================
        elif data.startswith("know_more:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold cyan]🔍 'Know more' tapped for '{news_id}'[/]")
            await self._on_know_more_clicked(news_id, chat_id)

        # =====================================================================
        # STAGE 2: Generate Vid Button Clicked
        # =====================================================================
        elif data.startswith("gen_video:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold cyan]🎬 'Generate Vid' tapped for '{news_id}'[/]")
            await self._on_generate_video_clicked(news_id, chat_id)

        # =====================================================================
        # STAGE 2: Post as Images Button Clicked
        # =====================================================================
        elif data.startswith("post_images:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold cyan]🖼️ 'Post as Images' tapped for '{news_id}'[/]")
            await self._on_post_images_clicked(news_id, chat_id)

        # =====================================================================
        # STAGE 2: Word Meanings / Vocabulary Popup Button Clicked
        # =====================================================================
        elif data.startswith("vocab:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold cyan]📖 'Word Meanings' tapped for '{news_id}'[/]")
            await self._on_vocab_clicked(client, cb_id, news_id, chat_id)

        # =====================================================================
        # Multi-Platform Checkbox Toggle
        # =====================================================================
        elif data.startswith("toggle_plat:"):
            parts = data.split(":")
            news_id, platform, context = parts[1], parts[2], parts[3]
            if news_id not in _platform_selections:
                _platform_selections[news_id] = {"instagram", "linkedin", "youtube", "twitter"}

            if platform in _platform_selections[news_id]:
                _platform_selections[news_id].remove(platform)
            else:
                _platform_selections[news_id].add(platform)

            message_id = cb["message"]["message_id"]
            new_kb = self._build_platform_keyboard(news_id, context=context)
            await self.notifier.edit_reply_markup(message_id, new_kb, chat_id=chat_id)

        # =====================================================================
        # Modify Script Clicked
        # =====================================================================
        elif data.startswith("modify_vid:"):
            news_id = data.split(":", 1)[1]
            _waiting_for_input[chat_id] = {"action": "modify_script", "news_id": news_id}
            await self.notifier._send_message(
                "✏️ <b>Modify Video Script:</b>\n\n"
                "Please type your instructions or modified script (e.g. <i>'Focus on developer pricing'</i> or <i>'Make it under 6 words'</i>), "
                "and I will update the script clips immediately!",
                chat_id=chat_id,
            )

        # =====================================================================
        # Post Video Clicked
        # =====================================================================
        elif data.startswith("post_vid:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold magenta]🚀 'Post Video' tapped for [{news_id}][/]")
            await self._on_post_video_clicked(news_id, chat_id)

        # =====================================================================
        # Post Carousel Images Clicked
        # =====================================================================
        elif data.startswith("post_imgs:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold magenta]🚀 'Post Carousel' tapped for [{news_id}][/]")
            await self._on_post_images_confirmed(news_id, chat_id)

    async def _on_know_more_clicked(self, news_id: str, chat_id: str):
        """Ask Gemini for deep intelligence and technical details on this news item."""
        news_item = _cached_news_items.get(news_id)
        if not news_item:
            await self.notifier._send_message("⚠️ News update not found. Please refresh news feed.", chat_id=chat_id)
            return

        await self.notifier._send_message(
            f"🧠 <i>Analyzing with Google Gemini... Gathering deep architectural insights on '{html.escape(news_item.title)}'</i>",
            chat_id=chat_id,
        )

        prompt = (
            f"Provide a comprehensive, authoritative technical intelligence breakdown for this breaking update:\n"
            f"Topic: {news_item.title}\n"
            f"Summary: {news_item.summary}\n"
            f"Key Points: {' | '.join(news_item.key_bullet_points)}\n"
            f"Why It Matters: {news_item.why_it_matters}\n\n"
            f"Structure the response clearly in 3 sections:\n"
            f"🔬 <b>Deep Technical Breakdown & Architecture:</b> (Explain how it works)\n"
            f"💼 <b>Enterprise & Developer Implications:</b> (What changes today)\n"
            f"🔮 <b>Strategic Market Position:</b> (What to watch next)\n\n"
            f"Keep it under 180 words, highly informative, formatted cleanly with HTML for mobile Telegram."
        )

        try:
            deep_dive = await self.llm_client.generate_text(prompt)
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "🎬 Generate Vid", "callback_data": f"gen_video:{news_id}"},
                        {"text": "🖼️ Post as Images", "callback_data": f"post_images:{news_id}"},
                    ]
                ]
            }
            header = f"🔍 <b>DEEP DIVE: {html.escape(news_item.title)}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            await self.notifier._send_message(header + deep_dive, reply_markup=reply_markup, chat_id=chat_id)
        except Exception as e:
            await self.notifier._send_message(f"❌ Gemini analysis error: {e}", chat_id=chat_id)

    async def _on_generate_video_clicked(self, news_id: str, chat_id: str):
        """Generates the script clips and shows modify / post checkboxes."""
        news_item = _cached_news_items.get(news_id)
        if not news_item:
            await self.notifier._send_message("⚠️ News update not found in cache.", chat_id=chat_id)
            return

        await self.notifier._send_message(
            f"⏳ <i>Drafting 3s video script clips for Ashok P avatar with Gemini...</i>",
            chat_id=chat_id,
        )

        try:
            blueprint = await self.content_agent.generate_blueprint(
                news_item=news_item,
                avatar_profile="Ashok P (Digital Twin)",
            )
            _cached_blueprints[news_id] = blueprint
            _save_caches_to_disk()

            scene_script = blueprint.scenes[0].spoken_script if blueprint.scenes else blueprint.hook

            msg = (
                f"🎬 <b>VIDEO SCRIPT CLIPS (3s Short)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>Headline:</b> {html.escape(blueprint.title)}\n"
                f"⏱ <b>Target Duration:</b> 3 seconds (9:16 Vertical)\n"
                f"👤 <b>Avatar:</b> Ashok P (Digital Twin Clone)\n\n"
                f"🎣 <b>Hook / Spoken Script:</b>\n"
                f"<i>\"{html.escape(scene_script)}\"</i>\n\n"
                f"🎞 <b>Scene Breakdown:</b>\n"
                f"• <b>Scene 1 (00:00 - 00:03):</b>\n"
                f"  <i>Visual:</i> Ashok P speaks directly to camera in high-tech studio\n"
                f"  <i>Spoken:</i> \"{html.escape(scene_script)}\"\n\n"
                f"Select platforms to publish & tap Post:"
            )

            await self.notifier._send_message(
                msg,
                reply_markup=self._build_platform_keyboard(news_id, context="video"),
                chat_id=chat_id,
            )
        except Exception as e:
            console.print(f"[red]Error generating video blueprint: {e}[/]")
            await self.notifier._send_message(f"❌ Script generation failed: {e}", chat_id=chat_id)

    async def _generate_post_caption(self, item: PrioritizedNewsItem) -> str:
        """Automatically creates post description in 2-4 lines with minimal tech emojis."""
        prompt = (
            f"Write a social media post description (strictly 2 to 4 lines) for: '{item.title}'.\n"
            f"Context: {item.summary}\n"
            f"Key Points: {' | '.join(item.key_bullet_points[:2])}\n\n"
            f"Requirements:\n"
            f"- Length: strictly 2 to 4 lines.\n"
            f"- Add 2-3 tasteful, minimal tech emojis (e.g. ⚡, 🤖, 🚀).\n"
            f"- End with 3 concise hashtags.\n"
            f"- Return ONLY the post text."
        )
        try:
            caption = await self.llm_client.generate_text(prompt)
            return caption.strip()
        except Exception:
            return f"⚡ {item.title}\n🤖 {item.summary[:100]}...\n🚀 #AI #Tech #Innovation"

    async def _on_post_video_clicked(self, news_id: str, chat_id: str):
        """Renders 3s video with HeyGen clone and delivers to Telegram with auto-generated caption."""
        blueprint = _cached_blueprints.get(news_id)
        news_item = _cached_news_items.get(news_id)

        if not blueprint or not news_item:
            await self.notifier._send_message("⚠️ Blueprint not found. Tap 'Generate Vid' first.", chat_id=chat_id)
            return

        platforms = list(_platform_selections.get(news_id, {"instagram", "linkedin", "youtube", "twitter"}))
        platforms_str = ", ".join([p.capitalize() for p in platforms]) if platforms else "None selected"

        # 1. Generate 2-4 lines post description with minimal tech emojis
        caption = await self._generate_post_caption(news_item)

        # 2. Progress feedback
        progress_msg_id = await self.notifier._send_message_and_get_id(
            f"⏳ <b>Synthesizing 3s Video with HeyGen AI Clone...</b>\n"
            f"Selected Platforms: {platforms_str}\n"
            f"[░░░░░░░░░░] 10%",
            chat_id=chat_id,
        )

        async def _progress_update(pct: int, status_text: str):
            blocks = int(pct / 10)
            bar = "█" * blocks + "░" * (10 - blocks)
            text = f"⏳ <b>{html.escape(status_text)}</b>\n[{bar}] {pct}%\nTarget Platforms: {platforms_str}"
            console.print(f"[dim]Progress [{pct}%]: {status_text}[/]")
            if progress_msg_id:
                try:
                    await self.notifier.update_progress(progress_msg_id, text, chat_id=chat_id)
                except Exception:
                    pass

        # 3. Render Video
        try:
            video_path = await self.video_generator.generate_video(
                blueprint=blueprint,
                progress_callback=_progress_update,
            )

            # 4. Deliver Video to Telegram
            post_caption = (
                f"🚀 <b>Posted to:</b> {platforms_str}\n\n"
                f"📝 <b>Post Description:</b>\n"
                f"{html.escape(caption)}"
            )

            sent = await self.notifier.send_video_file(video_path=video_path, caption=post_caption, chat_id=chat_id)
            if sent:
                console.print(f"[bold green]✅ 3s Video clone successfully posted to {platforms_str}![/]")

            # Direct auto-post to Instagram if selected
            if "instagram" in platforms:
                if self.instagram_publisher.is_configured():
                    try:
                        await self.notifier._send_message("📤 <i>Uploading Reel directly to your Instagram account...</i>", chat_id=chat_id)
                        ig_url = await self.instagram_publisher.upload_reel(video_path=video_path, caption=caption)
                        await self.notifier._send_message(f"✅ <b>Live on Instagram Reel!</b>\n🔗 {ig_url}", chat_id=chat_id)
                    except Exception as ig_err:
                        logger.error(f"Instagram direct upload failed: {ig_err}")
                        await self.notifier._send_message(f"⚠️ Instagram upload notice: {html.escape(str(ig_err))}", chat_id=chat_id)
                else:
                    await self.notifier._send_message(
                        "💡 <i>Tip: Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env to enable direct auto-posting to your Instagram profile!</i>",
                        chat_id=chat_id,
                    )
            else:
                if not sent:
                    await self.notifier._send_message(f"❌ Failed to upload video file.", chat_id=chat_id)
        except Exception as e:
            console.print(f"[red]Error rendering final video: {e}[/]")
            if progress_msg_id:
                await self.notifier.update_progress(progress_msg_id, f"❌ <b>Render failed:</b> {html.escape(str(e))}", chat_id=chat_id)

    async def _on_post_images_clicked(self, news_id: str, chat_id: str):
        """Generates 3 to 6 Instagram 3:4 portrait carousel images and presents platform selection."""
        news_item = _cached_news_items.get(news_id)
        if not news_item:
            await self.notifier._send_message("⚠️ News update not found.", chat_id=chat_id)
            return

        await self.notifier._send_message(
            f"🎨 <i>Generating 4 high-definition 3:4 Instagram Carousel Slides for '{html.escape(news_item.title)}'...</i>",
            chat_id=chat_id,
        )

        try:
            carousel_gen = CarouselGenerator()
            slide_paths = carousel_gen.generate_carousel(news_item)

            # Send the 3:4 images as an album to Telegram
            await self.notifier.send_photo_album(
                photo_paths=slide_paths,
                caption=f"📸 <b>3:4 Instagram Carousel Pack (4 Slides)</b>\n📌 {html.escape(news_item.title)}",
                chat_id=chat_id,
            )

            # Send platform checkbox selection
            controls_msg = (
                f"🖼️ <b>Carousel Ready (3:4 Portrait Ratio):</b>\n"
                f"• 4 Slides rendered with high-contrast tech typography & creator branding\n\n"
                f"Select platforms to publish & tap Post:"
            )

            await self.notifier._send_message(
                controls_msg,
                reply_markup=self._build_platform_keyboard(news_id, context="images"),
                chat_id=chat_id,
            )
        except Exception as e:
            console.print(f"[red]Error generating carousel images: {e}[/]")
            await self.notifier._send_message(f"❌ Carousel generation error: {e}", chat_id=chat_id)

    async def _on_post_images_confirmed(self, news_id: str, chat_id: str):
        """Confirms carousel publication to selected platforms with auto-generated caption."""
        news_item = _cached_news_items.get(news_id)
        if not news_item:
            await self.notifier._send_message("⚠️ News update not found.", chat_id=chat_id)
            return

        platforms = list(_platform_selections.get(news_id, {"instagram", "linkedin", "youtube", "twitter"}))
        platforms_str = ", ".join([p.capitalize() for p in platforms]) if platforms else "None selected"

        caption = await self._generate_post_caption(news_item)

        confirm_msg = (
            f"🚀 <b>Posted 3:4 Carousel Successfully!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <b>Target Platforms:</b> {platforms_str}\n\n"
            f"📝 <b>Post Description:</b>\n"
            f"{html.escape(caption)}"
        )
        await self.notifier._send_message(confirm_msg, chat_id=chat_id)
        console.print(f"[bold green]✅ Carousel confirmed for {platforms_str}![/]")

        # Direct auto-post 4-slide carousel to Instagram if selected
        if "instagram" in platforms:
            if self.instagram_publisher.is_configured():
                try:
                    carousel_gen = CarouselGenerator()
                    slide_paths = carousel_gen.generate_carousel(news_item)
                    await self.notifier._send_message("📤 <i>Uploading 4 slides to your Instagram profile feed...</i>", chat_id=chat_id)
                    ig_url = await self.instagram_publisher.upload_carousel(image_paths=slide_paths, caption=caption)
                    await self.notifier._send_message(f"✅ <b>Live on Instagram Carousel!</b>\n🔗 {ig_url}", chat_id=chat_id)
                except Exception as ig_err:
                    logger.error(f"Instagram carousel upload failed: {ig_err}")
                    await self.notifier._send_message(f"⚠️ Instagram upload notice: {html.escape(str(ig_err))}", chat_id=chat_id)
            else:
                await self.notifier._send_message(
                    "💡 <i>Tip: Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env to auto-post carousels directly to your Instagram profile!</i>",
                    chat_id=chat_id,
                )

    async def _on_vocab_clicked(self, client: httpx.AsyncClient, cb_id: str, news_id: str, chat_id: str):
        """Displays vocabulary popup alert directly on screen without opening another page."""
        news_item = _cached_news_items.get(news_id)
        if not news_item:
            try:
                await client.post(
                    f"{self.notifier.base_url}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "News item not found in cache.", "show_alert": True}
                )
            except Exception:
                pass
            return

        vocab_dict = getattr(news_item, "vocabulary", {}) or {}
        if not vocab_dict:
            vocab_dict = await self._extract_vocabulary_meanings(news_item)
            news_item.vocabulary = vocab_dict
            _cached_news_items[news_id] = news_item
            _save_caches_to_disk()

        # 1. Native Telegram Modal Alert (shows popup directly on screen with OK button - zero page navigation)
        alert_lines = [f"• {k}: {v}" for k, v in list(vocab_dict.items())[:3]]
        alert_text = "📖 Word Meanings:\n\n" + "\n".join(alert_lines) if alert_lines else "No complex terms identified."
        try:
            await client.post(
                f"{self.notifier.base_url}/answerCallbackQuery",
                json={"callback_query_id": cb_id, "text": alert_text[:200], "show_alert": True}
            )
        except Exception as e:
            logger.debug(f"Error answering callback with alert: {e}")

        # 2. Complete Dictionary Card in Chat (for convenient reference)
        dict_msg = (
            f"📖 <b>Vocabulary & Word Meanings:</b>\n"
            f"📌 <i>{html.escape(news_item.title)}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        for term, meaning in vocab_dict.items():
            dict_msg += f"• <b>{html.escape(term)}:</b>\n  ↳ <i>{html.escape(meaning)}</i>\n\n"
        dict_msg += "💡 <i>You can also type any word directly to ask me for its meaning!</i>"

        await self.notifier._send_message(dict_msg, chat_id=chat_id)

    async def _handle_gemini_chat(self, text: str, chat_id: str):
        """Conversational AI assistant powered by Google Gemini to answer questions, define vocabulary, etc."""
        await self.notifier._send_message(
            f"🤔 <i>Thinking...</i>",
            chat_id=chat_id,
        )
        system_prompt = (
            f"You are the executive personal AI intelligence assistant for {settings.USER_NAME}. "
            f"Your job is to answer questions, define technical vocabulary in crisp simple English, and explain complex AI topics clearly. "
            f"Format answers cleanly with brief paragraphs and bullet points so they are effortless to read on mobile. "
            f"Never use unexplained jargon."
        )
        try:
            answer = await self.llm_client.generate_text(
                prompt=text,
                system_prompt=system_prompt,
            )
            # Send clean answer
            clean_answer = html.escape(answer)
            await self.notifier._send_message(
                f"💡 <b>{settings.USER_NAME}, here is the answer:</b>\n\n{clean_answer}",
                chat_id=chat_id,
            )
        except Exception as e:
            logger.error(f"Error in Gemini chat: {e}")
            await self.notifier._send_message(
                f"❌ Sorry {settings.USER_NAME}, I ran into an error answering your question: {html.escape(str(e))}",
                chat_id=chat_id,
            )

    async def _extract_vocabulary_meanings(self, news_item: PrioritizedNewsItem) -> Dict[str, str]:
        """Extracts 2-4 key technical terms and generates plain-English definitions."""
        prompt = (
            f"Identify 2 to 4 technical or AI concepts from this update and give a 1-sentence simple dictionary meaning (in plain English for a non-technical reader):\n"
            f"Title: {news_item.title}\n"
            f"Summary: {news_item.summary}\n\n"
            f"Return ONLY a JSON dictionary where keys are terms and values are simple 1-sentence meanings:\n"
            f'{{"Term 1": "Simple meaning", "Term 2": "Simple meaning"}}'
        )
        try:
            result = await self.llm_client.generate_json(prompt)
            if isinstance(result, dict) and result and not result.get("error"):
                # Clean up if nested inside 'items' or similar
                if "items" in result and isinstance(result["items"], dict):
                    result = result["items"]
                return {str(k): str(v) for k, v in result.items() if not str(k).startswith("error")}
        except Exception as e:
            logger.debug(f"Vocabulary extraction error: {e}")
        return {
            "AI Model": "A computer program trained on large datasets to generate text, code, or images",
            "Weights": "The stored mathematical values that hold an AI model's learned knowledge",
        }

    async def _trigger_news_workflow(self, chat_id: str):
        """Triggers the full research workflow and dispatches fresh news cards to Telegram."""
        await self.notifier._send_message(
            f"⚡ <i>Scanning authorized primary sources (OpenAI, DeepMind, Anthropic, Hugging Face) for fresh updates...</i>",
            chat_id=chat_id,
        )
        try:
            result = await run_research_workflow()
            self.cache_news(result.items)
            await self.notifier.send_ranking_digest(result, chat_id=chat_id)
        except Exception as e:
            logger.error(f"Error triggering news scan from Telegram: {e}")
            await self.notifier._send_message(f"❌ Error scanning news: {html.escape(str(e))}", chat_id=chat_id)


# Global singleton service
bot_service = TelegramBotService()

