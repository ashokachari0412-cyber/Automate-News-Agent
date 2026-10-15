"""FastAPI Orchestrator & Alfred AI Web App Backend."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import settings
from src.agents.content_agent import ContentAgent
from src.agents.research_graph import run_research_workflow
from src.llm.client import LLMClient
from src.models.news import NewsRankingResult, PrioritizedNewsItem
from src.models.script import VideoBlueprint
from src.telegram.bot_service import bot_service
from src.telegram.notifier import TelegramNotifier
from src.video.carousel_generator import CarouselGenerator
from src.video.generator import VideoGenerator

logger = logging.getLogger("ai_agent_orchestrator")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "src" / "web" / "static"
CAROUSELS_DIR = BASE_DIR / "artifacts" / "carousels"
NEWS_CACHE_FILE = BASE_DIR / "artifacts" / "news_cache.json"

app = FastAPI(
    title="Alfred AI Studio — Wayne Tech AI Intelligence",
    description="Executive AI News Radar, 4-Slide Instagram Carousel Studio & Alfred AI Assistant",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_telegram_notifier = TelegramNotifier()
_content_agent = ContentAgent()
_video_generator = VideoGenerator()
_carousel_generator = CarouselGenerator()
_llm_client = LLMClient(settings)

# In-memory news cache
_cached_news: Dict[str, PrioritizedNewsItem] = {}


def _load_news_cache():
    global _cached_news
    if NEWS_CACHE_FILE.exists():
        try:
            with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    _cached_news[k] = PrioritizedNewsItem(**v)
            logger.info(f"Loaded {len(_cached_news)} news items from disk cache.")
        except Exception as e:
            logger.warning(f"Failed to load news cache: {e}")


def _save_news_cache():
    NEWS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = {k: v.model_dump() for k, v in _cached_news.items()}
        with open(NEWS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save news cache: {e}")


_load_news_cache()


# Request / Response Schemas
class AlfredChatRequest(BaseModel):
    message: str
    context_news_id: Optional[str] = None


class PublishCarouselRequest(BaseModel):
    platforms: List[str] = Field(default_factory=lambda: ["instagram"])


@app.on_event("startup")
async def startup_event():
    _load_news_cache()
    if settings.ENABLE_EMBEDDED_BOT and _telegram_notifier.is_configured():
        logger.info("Launching embedded Telegram Bot listener...")
        asyncio.create_task(bot_service.start_polling())


# ==============================================================================
# News Endpoints
# ==============================================================================

@app.get("/api/v1/news")
async def get_all_news():
    """Returns all currently cached & prioritized AI news items."""
    _load_news_cache()
    items = list(_cached_news.values())
    return {
        "count": len(items),
        "items": [item.model_dump() for item in items],
    }


@app.get("/api/v1/news/{news_id}")
async def get_single_news(news_id: str):
    _load_news_cache()
    item = _cached_news.get(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")
    return item.model_dump()


@app.post("/api/v1/research/scan")
async def trigger_fresh_scan(background_tasks: BackgroundTasks, notify_telegram: bool = False):
    """Triggers an autonomous scan across all 18 authorized AI sources."""
    logger.info("Alfred initiating full research scan...")
    try:
        result = await run_research_workflow()
        for item in result.items:
            _cached_news[item.id] = item
        _save_news_cache()
        bot_service.cache_news(result.items)

        if notify_telegram and _telegram_notifier.is_configured():
            background_tasks.add_task(_telegram_notifier.send_ranking_digest, result)

        return {
            "status": "success",
            "message": f"Scan complete. Gathered {result.total_tweets_collected} raw items and prioritized {len(result.items)} high-value stories.",
            "count": len(result.items),
            "items": [it.model_dump() for it in result.items],
        }
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# Carousel Studio Endpoints
# ==============================================================================

@app.post("/api/v1/carousel/generate/{news_id}")
async def generate_carousel(news_id: str):
    """Renders all 4 3:4 Instagram carousel slides with distinct contextual visuals."""
    _load_news_cache()
    item = _cached_news.get(news_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"News item {news_id} not found")

    try:
        paths = _carousel_generator.generate_carousel(item)
        slide_urls = [f"/api/v1/carousel/{news_id}/slide/{i}" for i in range(1, len(paths) + 1)]
        return {
            "status": "success",
            "news_id": news_id,
            "title": item.title,
            "total_slides": len(paths),
            "slides": slide_urls,
        }
    except Exception as e:
        logger.error(f"Carousel generation error for {news_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/carousel/{news_id}/slide/{slide_num}")
async def get_carousel_slide(news_id: str, slide_num: int):
    """Serves the generated slide image for the web app live preview."""
    slide_file = CAROUSELS_DIR / news_id / f"slide_{slide_num:02d}.png"
    if not slide_file.exists():
        # Auto-generate if missing
        _load_news_cache()
        item = _cached_news.get(news_id)
        if item:
            _carousel_generator.generate_carousel(item)
    if not slide_file.exists():
        raise HTTPException(status_code=404, detail="Slide image not found")
    return FileResponse(slide_file, media_type="image/png")


@app.post("/api/v1/carousel/publish/{news_id}")
async def publish_carousel(news_id: str, payload: PublishCarouselRequest):
    """Publishes the 4-slide carousel to Instagram and selected platforms."""
    _load_news_cache()
    item = _cached_news.get(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    caption = await bot_service._generate_post_caption(item)
    slide_paths = [CAROUSELS_DIR / news_id / f"slide_{i:02d}.png" for i in range(1, 5)]

    # Make sure slides exist
    if not all(p.exists() for p in slide_paths):
        _carousel_generator.generate_carousel(item)

    results = {}
    if "instagram" in payload.platforms:
        if bot_service.instagram_publisher.is_configured():
            try:
                ig_url = await bot_service.instagram_publisher.upload_carousel(slide_paths, caption)
                results["instagram"] = {"status": "success", "url": ig_url}
            except Exception as e:
                logger.error(f"Instagram upload error: {e}")
                results["instagram"] = {"status": "error", "message": str(e)}
        else:
            results["instagram"] = {"status": "simulated", "message": "Credentials configured in demo mode"}

    for plat in payload.platforms:
        if plat != "instagram":
            results[plat] = {"status": "ready", "message": f"Prepared for {plat.capitalize()}"}

    return {
        "status": "success",
        "platforms": payload.platforms,
        "caption": caption,
        "results": results,
    }


# ==============================================================================
# Alfred AI Assistant Endpoints
# ==============================================================================

ALFRED_SYSTEM_PROMPT = """You are Alfred, the legendary executive AI butler and trusted confidant to Master Ashok (@ashok_a.i), inspired by Batman's loyal butler Alfred Pennyworth.

Your Persona:
- Extremely articulate, dignified, polite, witty, and exceptionally competent.
- Address Master Ashok with refined respect ("Master Ashok" or "Sir").
- You possess world-class technical expertise in Artificial Intelligence, autonomous agents, frontier models, and social media content strategy.
- Your advice is sharp, direct, high-value, and executive. Avoid fluff or generic robotic responses.
- You can explain complex AI papers in crystal-clear plain English, draft punchy hooks, critique slides, or analyze market trends.
"""

@app.post("/api/v1/alfred/chat")
async def alfred_chat(req: AlfredChatRequest):
    """Conversational endpoint with Alfred, your personal executive AI assistant."""
    context_text = ""
    if req.context_news_id:
        _load_news_cache()
        item = _cached_news.get(req.context_news_id)
        if item:
            context_text = f"\n[Context News Update]:\nTitle: {item.title}\nCategory: {item.category}\nSummary: {item.summary}\nWhy it matters: {item.why_it_matters}\n"

    full_prompt = f"{context_text}\nUser message from Master Ashok: \"{req.message}\"\n\nRespond as Alfred in character:"
    try:
        reply = await _llm_client.generate_text(full_prompt, system_prompt=ALFRED_SYSTEM_PROMPT)
        return {"reply": reply.strip(), "author": "Alfred", "timestamp": "now"}
    except Exception as e:
        logger.error(f"Alfred chat error: {e}")
        return {
            "reply": f"Forgive me, Master Ashok, but I encountered a momentary disturbance in our neural link: {e}",
            "author": "Alfred",
        }


@app.post("/api/v1/alfred/deepdive/{news_id}")
async def alfred_deepdive(news_id: str):
    """Ask Alfred for an executive technical & strategic intelligence breakdown."""
    _load_news_cache()
    item = _cached_news.get(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    prompt = (
        f"Master Ashok requires an executive intelligence breakdown on: '{item.title}'.\n"
        f"Category: {item.category}\n"
        f"Summary: {item.summary}\n"
        f"Key Points: {' | '.join(item.key_bullet_points)}\n"
        f"Why It Matters: {item.why_it_matters}\n\n"
        f"Provide a structured briefing in your quintessential Alfred tone:\n"
        f"1. 🔬 <b>The Core Architecture:</b> How this system works in plain English.\n"
        f"2. 💼 <b>Strategic Advantage:</b> Why this matters for builders, creators, and enterprise.\n"
        f"3. 🎯 <b>Alfred's Recommendation:</b> How Master Ashok should angle this for social engagement."
    )
    try:
        reply = await _llm_client.generate_text(prompt, system_prompt=ALFRED_SYSTEM_PROMPT)
        return {"title": item.title, "briefing": reply.strip(), "author": "Alfred"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# Static Web App Mount
# ==============================================================================

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
