"""FastAPI Orchestrator Layer (Box 4 in Architecture)."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config.settings import settings
from src.agents.content_agent import ContentAgent
from src.agents.research_graph import run_research_workflow
from src.models.news import NewsRankingResult, PrioritizedNewsItem
from src.models.script import VideoBlueprint
from src.telegram.bot_service import bot_service
from src.telegram.notifier import TelegramNotifier
from src.video.generator import VideoGenerator

logger = logging.getLogger("ai_agent_orchestrator")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Personal AI Content Agent - Orchestrator",
    description="Automated AI Research, Prioritization & 35-40s Video Generation Pipeline (Google-Powered)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_latest_research_result: Optional[NewsRankingResult] = None
_telegram_notifier = TelegramNotifier()
_content_agent = ContentAgent()
_video_generator = VideoGenerator()


class TriggerResearchResponse(BaseModel):
    status: str
    message: str
    result: Optional[NewsRankingResult] = None


@app.on_event("startup")
async def startup_event():
    """Optionally starts Telegram background polling listener on server start."""
    if settings.ENABLE_EMBEDDED_BOT and _telegram_notifier.is_configured():
        logger.info("Launching embedded Telegram Bot listener in background task...")
        asyncio.create_task(bot_service.start_polling())
    else:
        logger.info("Embedded Telegram bot disabled (running as dedicated service).")


@app.get("/")
async def root():
    return {
        "service": "Personal AI Content Agent",
        "phase": "Steps 1 & 2: AI News Prioritization & 35-40s Video Generation",
        "orchestrator": "FastAPI + LangGraph",
        "llm_provider": f"Google Gemini ({settings.GEMINI_MODEL})",
        "collector_mode": settings.COLLECTOR_MODE,
        "telegram_configured": _telegram_notifier.is_configured(),
        "docs_url": "/docs",
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "automate-news-agent"}


@app.get("/api/v1/accounts")
async def get_trusted_accounts():
    accounts = settings.get_trusted_accounts()
    return {"count": len(accounts), "accounts": accounts}


@app.post("/api/v1/research/run", response_model=TriggerResearchResponse)
async def trigger_research(
    background_tasks: BackgroundTasks,
    send_telegram: bool = True,
):
    """Trigger the LangGraph AI Research and Prioritization agent (Strict 24h window)."""
    global _latest_research_result
    logger.info("Triggering AI Research & Prioritization workflow...")

    try:
        result = await run_research_workflow()
        _latest_research_result = result
        bot_service.cache_news(result.items)

        if send_telegram and _telegram_notifier.is_configured():
            background_tasks.add_task(_telegram_notifier.send_ranking_digest, result)

        return TriggerResearchResponse(
            status="success",
            message=f"Analyzed {result.total_tweets_collected} posts. Found {len(result.items)} prioritized news items.",
            result=result,
        )
    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/research/latest", response_model=Optional[NewsRankingResult])
async def get_latest_research():
    if not _latest_research_result:
        raise HTTPException(status_code=404, detail="No research results yet. Call POST /api/v1/research/run first.")
    return _latest_research_result


@app.post("/api/v1/video/blueprint/{news_id}", response_model=VideoBlueprint)
async def generate_blueprint(news_id: str, avatar: str = "Tech Founder (Professional / AI Pioneer)"):
    """Generate 35-40s video script and scene clips for a chosen news story."""
    if not _latest_research_result:
        raise HTTPException(status_code=404, detail="No research results found. Run research first.")

    item = next((it for it in _latest_research_result.items if it.id == news_id), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"News item {news_id} not found.")

    blueprint = await _content_agent.generate_blueprint(news_item=item, avatar_profile=avatar)
    return blueprint


@app.post("/api/v1/video/generate/{news_id}")
async def generate_final_video(news_id: str, background_tasks: BackgroundTasks, send_telegram: bool = True):
    """Render the final 35-40s vertical MP4 video and deliver to Telegram."""
    if not _latest_research_result:
        raise HTTPException(status_code=404, detail="No research results found. Run research first.")

    item = next((it for it in _latest_research_result.items if it.id == news_id), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"News item {news_id} not found.")

    blueprint = await _content_agent.generate_blueprint(news_item=item)

    async def _render_and_send():
        video_path = await _video_generator.generate_video(blueprint=blueprint)
        if send_telegram and _telegram_notifier.is_configured():
            await _telegram_notifier.send_video_file(
                video_path=video_path,
                caption=f"🎬 <b>{item.title}</b> ({blueprint.estimated_duration_seconds}s short)",
            )

    background_tasks.add_task(_render_and_send)
    return {"status": "started", "message": f"Rendering 35-40s video for '{item.title}'"}


@app.post("/api/v1/telegram/webhook")
async def telegram_webhook(update: Dict[str, Any]):
    """Receives Telegram webhook updates and executes button callbacks."""
    async with httpx.AsyncClient() as client:
        await bot_service._handle_update(client, update)
    return {"status": "ok"}
