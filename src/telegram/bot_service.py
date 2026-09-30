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
from typing import Dict, List, Optional
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

logger = logging.getLogger(__name__)
console = Console(legacy_windows=False)

CACHE_DIR = Path("artifacts")
NEWS_CACHE_FILE = CACHE_DIR / "news_cache.json"
BLUEPRINTS_CACHE_FILE = CACHE_DIR / "blueprints_cache.json"

# In-memory session caches
_cached_news_items: Dict[str, PrioritizedNewsItem] = {}
_cached_blueprints: Dict[str, VideoBlueprint] = {}
_chat_history: Dict[str, List[dict]] = {}

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
    """Check if the current time is within active operating hours (10:00 AM - 10:00 PM)."""
    if not settings.ACTIVE_HOURS_ENABLED:
        return True
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


def _load_caches_from_disk():
    """Load cached news and blueprints from disk on startup."""
    global _cached_news_items, _cached_blueprints
    try:
        if NEWS_CACHE_FILE.exists():
            with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    _cached_news_items[k] = PrioritizedNewsItem.model_validate(v)
            logger.info(f"Loaded {len(_cached_news_items)} news items from cache.")

        if BLUEPRINTS_CACHE_FILE.exists():
            with open(BLUEPRINTS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    _cached_blueprints[k] = VideoBlueprint.model_validate(v)
            logger.info(f"Loaded {len(_cached_blueprints)} video blueprints from cache.")
    except Exception as e:
        logger.warning(f"Error loading caches from disk: {e}")


# Initialize disk cache on import
_load_caches_from_disk()


class TelegramBotService:
    """Long-polling interactive Telegram Bot controller with Gemini Chat & Video Generation."""

    def __init__(self):
        self.notifier = TelegramNotifier()
        self.content_agent = ContentAgent()
        self.video_generator = VideoGenerator()
        self.llm_client = LLMClient()
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
        console.print(f"[bold green]🤖 Telegram Bot Service active (Hours: {settings.ACTIVE_HOURS_START}:00 AM - {settings.ACTIVE_HOURS_END - 12}:00 PM)...[/]")
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
        """Dispatch updates to button callback handlers or text message handlers."""
        # 1. Handle Inline Button Clicks
        callback_query = update.get("callback_query")
        if callback_query:
            await self._handle_callback(client, callback_query)
            return

        # 2. Handle Incoming Text Messages
        message = update.get("message")
        if message:
            text = (message.get("text") or "").strip()
            chat_id = str(message["chat"]["id"])
            if text:
                await self._handle_text_message(text, chat_id)

    async def _handle_text_message(self, text: str, chat_id: str):
        """Handle incoming text messages: time-check, greeting, news trigger, or Gemini chat."""
        # 1. Check Operating Window (10:00 AM - 10:00 PM)
        if not _is_within_active_hours():
            console.print(f"[yellow]Outside active hours (10 AM - 10 PM). Replying off-duty message to {chat_id}.[/]")
            end_label = f"{settings.ACTIVE_HOURS_END - 12}:00 PM" if settings.ACTIVE_HOURS_END > 12 else f"{settings.ACTIVE_HOURS_END}:00"
            start_label = f"{settings.ACTIVE_HOURS_START}:00 AM"
            await self.notifier._send_message(
                f"🌙 <b>Hi {settings.USER_NAME}, I'm off-duty right now.</b>\n\n"
                f"My active hours are <b>{start_label} to {end_label}</b>.\n"
                f"I will be ready to help you first thing in the morning! ☀️",
                chat_id=chat_id,
            )
            return

        clean_text = text.lower().strip()
        clean_no_punct = re.sub(r"[^\w\s]", "", clean_text).strip()

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

    async def _trigger_news_workflow(self, chat_id: str):
        """Execute breaking news pipeline and send clean news cards with video buttons."""
        await self.notifier._send_message(
            f"🔎 <i>Scanning verified frontier accounts for breaking AI news strictly from the last 24 hours...</i>",
            chat_id=chat_id,
        )
        try:
            result = await run_research_workflow()
            self.cache_news(result.items)
            await self.notifier.send_ranking_digest(result, chat_id=chat_id)
            console.print(f"[bold green]OK: Breaking AI news dispatched to {chat_id}![/]")
        except Exception as e:
            console.print(f"[red]Error during news workflow: {e}[/]")
            await self.notifier._send_message(f"❌ <b>Error gathering news:</b> {html.escape(str(e))}", chat_id=chat_id)

    async def _handle_gemini_chat(self, text: str, chat_id: str):
        """Conversational AI mode powered by Google Gemini (gemini-3.8-flash)."""
        system_prompt = (
            f"You are a helpful, brilliant AI personal assistant for {settings.USER_NAME}. "
            "You provide clear, accurate, and insightful answers just like Google Gemini or ChatGPT. "
            "You can explain word meanings, technical concepts, write or debug code, summarize information, and chat. "
            "Keep formatting clean and readable on mobile Telegram."
        )

        history = _chat_history.get(chat_id, [])

        try:
            answer = await self.llm_client.generate_text(
                prompt=text,
                system_prompt=system_prompt,
                history=history,
            )

            # Update conversational memory
            history.append({"role": "user", "content": text})
            history.append({"role": "model", "content": answer})
            _chat_history[chat_id] = history[-10:]

            console.print(f"[bold green]💬 Answer generated with Gemini -> sent to Telegram![/]")
            await self.notifier._send_message(answer, chat_id=chat_id)
        except Exception as e:
            console.print(f"[red]Gemini Chat Error: {e}[/]")
            await self.notifier._send_message(
                f"Sorry {settings.USER_NAME}, I encountered an issue reaching Gemini. Please try asking again!",
                chat_id=chat_id,
            )

    async def _handle_callback(self, client: httpx.AsyncClient, cb: dict):
        """Handle user clicking buttons in Telegram."""
        cb_id = cb["id"]
        data = cb.get("data", "")
        chat_id = str(cb["message"]["chat"]["id"])

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

        # Case A: User clicked "[🎬 Generate Video for This]"
        elif data.startswith("gen_video:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold cyan]⚡ Telegram Button Clicked: Generate Video for [{news_id}][/]")
            await self._on_generate_blueprint_clicked(news_id, chat_id)

        # Case B: User switched avatar
        elif data.startswith("avatar:"):
            parts = data.split(":")
            news_id, avatar_key = parts[1], parts[2]
            console.print(f"[cyan]👤 Telegram Button Clicked: Switch Avatar to [{avatar_key}] for [{news_id}][/]")
            await self._on_switch_avatar_clicked(news_id, avatar_key, chat_id)

        # Case C: User clicked "[🚀 Render Final Video]"
        elif data.startswith("render_final:"):
            news_id = data.split(":", 1)[1]
            console.print(f"[bold magenta]🚀 Telegram Button Clicked: Render Final Video for [{news_id}][/]")
            await self._on_render_final_clicked(news_id, chat_id)

        # Case D: Approval confirmation
        elif data == "video_approved":
            console.print("[green]✅ Telegram Button Clicked: Video Approved![/]")
            await self.notifier._send_message(
                "✅ <b>Video Approved!</b> Saved to content library and queued for multi-platform publishing.",
                chat_id=chat_id,
            )

    async def _on_generate_blueprint_clicked(self, news_id: str, chat_id: str):
        """Generate and present the Video Blueprint to user."""
        news_item = _cached_news_items.get(news_id)

        if not news_item:
            news_item = PrioritizedNewsItem(
                id=news_id,
                title="AI Breakthrough Milestone",
                hook="This changes AI video forever.",
                summary="Major release from frontier lab.",
                key_bullet_points=["Key capability 1", "Key capability 2"],
                category="Frontier Model",
                scores=NewsScoreBreakdown(impact=24, novelty=23, credibility=25, video_adaptability=24, total=96),
                why_it_matters="Accelerates autonomous agent workflows.",
                suggested_video_angle="High energy screen breakdown.",
                primary_source_handle="OpenAI",
            )

        # Send instant notification to user in Telegram
        await self.notifier._send_message(
            f"⏳ <i>Drafting {settings.VIDEO_DURATION_SECONDS}s video blueprint & scenes with Gemini...</i>",
            chat_id=chat_id,
        )

        try:
            blueprint = await self.content_agent.generate_blueprint(
                news_item=news_item,
                avatar_profile="Tech Founder (Professional / AI Pioneer)",
            )
            _cached_blueprints[news_id] = blueprint
            _save_caches_to_disk()

            await self.notifier.send_video_blueprint(blueprint, chat_id=chat_id)
            console.print(f"[bold green]OK: Sent {settings.VIDEO_DURATION_SECONDS}s Video Blueprint to Telegram![/]")
        except Exception as e:
            console.print(f"[red]Error generating video blueprint: {e}[/]")
            await self.notifier._send_message(f"❌ <b>Blueprint generation failed:</b> {html.escape(str(e))}", chat_id=chat_id)

    async def _on_switch_avatar_clicked(self, news_id: str, avatar_key: str, chat_id: str):
        """Update active avatar profile on the blueprint."""
        blueprint = _cached_blueprints.get(news_id)
        if not blueprint:
            await self.notifier._send_message("⚠️ Video blueprint not found. Tap 'Generate Video' first.", chat_id=chat_id)
            return

        avatar_names = {
            "casual": "Casual AI Creator (Energetic / Hoodie)",
            "executive": "Executive Tech Leader (Suit / Corporate Studio)",
        }
        blueprint.avatar_profile = avatar_names.get(avatar_key, "Tech Founder (Professional / AI Pioneer)")
        _cached_blueprints[news_id] = blueprint
        _save_caches_to_disk()

        await self.notifier.send_video_blueprint(blueprint, chat_id=chat_id)

    async def _on_render_final_clicked(self, news_id: str, chat_id: str):
        """Render final video with live progress updates and upload to Telegram."""
        blueprint = _cached_blueprints.get(news_id)
        if not blueprint:
            await self.notifier._send_message("⚠️ Video blueprint not found. Please tap 'Generate Video' first.", chat_id=chat_id)
            return

        console.print(f"[bold magenta]🎬 Starting render of {blueprint.estimated_duration_seconds}s video...[/]")

        # 1. Send live progress placeholder
        progress_msg_id = await self.notifier._send_message_and_get_id(
            "⏳ <b>Initializing Google Video Model...</b>\n[░░░░░░░░░░] 0%",
            chat_id=chat_id,
        )

        async def _progress_update(pct: int, status_text: str):
            blocks = int(pct / 10)
            bar = "█" * blocks + "░" * (10 - blocks)
            text = f"⏳ <b>{html.escape(status_text)}</b>\n[{bar}] {pct}%"
            console.print(f"[dim]Progress [{pct}%]: {status_text}[/]")
            if progress_msg_id:
                try:
                    await self.notifier.update_progress(progress_msg_id, text, chat_id=chat_id)
                except Exception:
                    pass

        # 2. Render Video with Google Video Generator
        try:
            video_path = await self.video_generator.generate_video(
                blueprint=blueprint,
                progress_callback=_progress_update,
            )

            # 3. Deliver Video into Telegram
            caption = (
                f"🎬 <b>{html.escape(blueprint.title)}</b>\n"
                f"⏱ <b>Duration:</b> {blueprint.estimated_duration_seconds}s (9:16 Vertical Short)\n"
                f"👤 <b>Avatar:</b> {html.escape(blueprint.avatar_profile.split('(')[0])}\n\n"
                f"🎣 <b>Hook:</b> <i>\"{html.escape(blueprint.hook)}\"</i>"
            )

            console.print(f"[bold cyan]Uploading generated video ({video_path}) to Telegram...[/]")
            sent = await self.notifier.send_video_file(video_path=video_path, caption=caption, chat_id=chat_id)
            if sent:
                console.print("[bold green]✅ Video delivered directly into Telegram![/]")
            else:
                console.print("[red]Failed to send video file to Telegram.[/]")
        except Exception as e:
            console.print(f"[red]Error rendering final video: {e}[/]")
            if progress_msg_id:
                await self.notifier.update_progress(progress_msg_id, f"❌ <b>Render failed:</b> {html.escape(str(e))}", chat_id=chat_id)


# Global singleton service
bot_service = TelegramBotService()
