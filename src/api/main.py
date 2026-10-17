"""FastAPI Orchestrator & Alfred AI Web App Backend."""

import asyncio
import base64
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import settings
from src.agents.content_agent import ContentAgent
from src.agents.research_graph import run_research_workflow
from src.llm.client import LLMClient
from src.models.news import NewsRankingResult, PrioritizedNewsItem
from src.models.script import VideoBlueprint
from src.publishers.instagram_publisher import InstagramPublisher
from src.publishers.linkedin_publisher import LinkedInPublisher
from src.publishers.youtube_publisher import YouTubePublisher
from src import progress, storage
from src.google_workspace import auth as google_auth
from src.google_workspace.agent import is_workspace_query, run_workspace_agent
from src.google_workspace.router import router as google_router
from src.video.carousel_generator import CarouselGenerator
from src.video.generator import VideoGenerator

logger = logging.getLogger("ai_agent_orchestrator")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "src" / "web" / "static"
CAROUSELS_DIR = BASE_DIR / "artifacts" / "carousels"

app = FastAPI(
    title="Alfred AI Studio — Wayne Tech AI Intelligence",
    description="Executive AI News Radar, 4-Slide Instagram Carousel Studio & Alfred AI Assistant",
    version="2.0.0",
)

app.include_router(google_router)


PUBLIC_PWA_PATHS = {"/manifest.webmanifest", "/sw.js", "/favicon.png", "/logo.png",
                    "/icon-192.png", "/icon-512.png", "/icon-512-maskable.png"}


@app.middleware("http")
async def basic_auth(request, call_next):
    """Protect the app with HTTP Basic auth when APP_PASSWORD is set (needed for public tunnels)."""
    password = (getattr(settings, "APP_PASSWORD", None) or os.getenv("APP_PASSWORD", "")).strip()
    # Install files stay public: browsers fetch the manifest/icons/service worker without credentials,
    # and a 401 there makes the PWA "cannot be installed". They contain nothing private.
    if not password or request.url.path in PUBLIC_PWA_PATHS:
        return await call_next(request)
    try:
        supplied = base64.b64decode(request.headers.get("authorization", "")[6:]).decode().partition(":")[2]
    except Exception:
        supplied = ""
    if not secrets.compare_digest(supplied.encode(), password.encode()):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Alfred AI"'})
    return await call_next(request)


@app.middleware("http")
async def no_stale_web_app(request, call_next):
    """Make browsers revalidate the web app files so UI updates show up immediately."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_publishers = {
    "instagram": InstagramPublisher(),
    "linkedin": LinkedInPublisher(),
    "youtube": YouTubePublisher(),
}
PUBLISHER_SETUP_HINTS = {
    "instagram": "Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env",
    "linkedin": "Set LINKEDIN_ACCESS_TOKEN in .env",
    "youtube": "Reconnect Google (Connect Google button) to grant YouTube upload access",
}
_content_agent = ContentAgent()
_video_generator = VideoGenerator()
_carousel_generator = CarouselGenerator()
_llm_client = LLMClient(settings)

# In-memory news cache
_cached_news: Dict[str, PrioritizedNewsItem] = {}


def _load_news_cache():
    _cached_news.update(storage.load_news())


def _save_news_cache():
    try:
        storage.save_news(_cached_news)
    except Exception as e:
        logger.warning(f"Failed to save news cache: {e}")


async def _generate_post_caption(item: PrioritizedNewsItem) -> str:
    """Creates a 2-4 line social post caption with minimal tech emojis."""
    progress.report("Writing the post caption and hashtags...")
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
        return (await _llm_client.generate_text(prompt)).strip()
    except Exception:
        return f"⚡ {item.title}\n🤖 {item.summary[:100]}...\n🚀 #AI #Tech #Innovation"


_load_news_cache()


# Request / Response Schemas
class AlfredChatRequest(BaseModel):
    message: str
    attachment_name: Optional[str] = None
    attachment_text: Optional[str] = None
    job_id: Optional[str] = None  # lets the UI poll /api/v1/progress/{job_id} for live steps
    context_news_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None


class PublishCarouselRequest(BaseModel):
    platforms: List[str] = Field(default_factory=lambda: ["instagram"])
    format: str = "carousel"  # "carousel" (4 Instagram images) or "single" (1 image)
    brand: str = "instagram"  # single image only: "instagram" (@ashok_a.i branded) or "linkedin" (content only)


@app.on_event("startup")
async def startup_event():
    _load_news_cache()


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
async def trigger_fresh_scan():
    """Triggers an autonomous scan across all 18 authorized AI sources."""
    logger.info("Alfred initiating full research scan...")
    try:
        result = await run_research_workflow()
        for item in result.items:
            _cached_news[item.id] = item
        _save_news_cache()

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
        paths = await _carousel_generator.generate_carousel_async(item)
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


@app.get("/api/v1/carousel/{news_id}/single")
async def get_single_image(news_id: str, brand: str = "instagram"):
    """Serves the single all-in-one image (Instagram-branded or plain LinkedIn)."""
    brand = "linkedin" if brand == "linkedin" else "instagram"
    single_file = CAROUSELS_DIR / news_id / f"single_{brand}.png"
    if not single_file.exists():
        raise HTTPException(status_code=404, detail="Single image not found")
    return FileResponse(single_file, media_type="image/png")


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

    caption = await _generate_post_caption(item)
    if payload.format == "single":
        brand = "linkedin" if payload.brand == "linkedin" else "instagram"
        slide_paths = [CAROUSELS_DIR / news_id / f"single_{brand}.png"]
        if not slide_paths[0].exists():
            await _carousel_generator.generate_single_async(item, brand)
    else:
        slide_paths = [CAROUSELS_DIR / news_id / f"slide_{i:02d}.png" for i in range(1, 5)]
        # Make sure slides exist
        if not all(p.exists() for p in slide_paths):
            await _carousel_generator.generate_carousel_async(item)

    results = {}
    for plat in payload.platforms:
        publisher = _publishers.get(plat)
        if not publisher:
            results[plat] = {"status": "error", "message": f"Unsupported platform: {plat}"}
        elif not publisher.is_configured():
            results[plat] = {"status": "not_configured", "message": PUBLISHER_SETUP_HINTS[plat]}
        else:
            try:
                if plat == "youtube":
                    url = await publisher.upload_carousel(slide_paths, caption, item.title)
                else:
                    url = await publisher.upload_carousel(slide_paths, caption)
                results[plat] = {"status": "success", "url": url}
            except Exception as e:
                logger.error(f"{plat} upload error: {e}")
                results[plat] = {"status": "error", "message": str(e)}

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

def _ranked_news() -> List[PrioritizedNewsItem]:
    items = list(_cached_news.values())
    items.sort(key=lambda x: getattr(getattr(x, "scores", None), "total", 0), reverse=True)
    return items


ATTACHMENT_MAX_CHARS = 60000
ATTACHMENT_MAX_BYTES = 15 * 1024 * 1024


def _extract_attachment_text(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    if b"\x00" in data[:4096]:
        raise ValueError("Unsupported file type. Attach a PDF, Word (.docx) or text/code file.")
    return data.decode("utf-8-sig", errors="replace")


@app.post("/api/v1/alfred/attach")
async def attach_file(file: UploadFile = File(...)):
    """Extracts text from an attached file so the next chat message can use it."""
    data = await file.read()
    if len(data) > ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 15 MB.")
    try:
        text = (await asyncio.to_thread(_extract_attachment_text, file.filename or "file", data)).strip()
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read file: {e}")
    if not text:
        raise HTTPException(status_code=422, detail="No readable text found in this file.")
    return {"name": file.filename, "chars": len(text), "truncated": len(text) > ATTACHMENT_MAX_CHARS,
            "text": text[:ATTACHMENT_MAX_CHARS]}


@app.get("/api/v1/progress/{job_id}")
async def get_progress(job_id: str):
    """Latest human-readable step of a running chat job (polled by the UI)."""
    return {"message": progress.get(job_id)}


@app.get("/api/v1/alfred/memory")
async def get_alfred_memory():
    """Returns the persisted conversation so the UI can restore it on reload."""
    return storage.load_memory()


@app.delete("/api/v1/alfred/memory")
async def clear_alfred_memory():
    """New chat: the current conversation is saved to history (last 15 sessions) and cleared."""
    storage.archive_current_session()
    return {"status": "cleared"}


@app.get("/api/v1/alfred/sessions")
async def list_chat_sessions():
    return {"sessions": storage.list_sessions()}


@app.post("/api/v1/alfred/sessions/{session_id}/open")
async def open_chat_session(session_id: str):
    memory = storage.open_session(session_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return memory


@app.post("/api/v1/alfred/chat")
async def alfred_chat(req: AlfredChatRequest):
    """Conversational endpoint with Alfred; every turn is persisted to disk as memory."""
    _load_news_cache()
    memory = storage.load_memory()
    history = [{"role": m["role"], "content": m["content"]} for m in memory.get("messages", [])]

    progress.start(req.job_id)
    try:
        response = await _handle_chat(req, history, memory.get("last_news_id"))
    finally:
        progress.finish(req.job_id)

    memory.setdefault("messages", []).append({"role": "user", "content": req.message})
    memory["messages"].append({"role": "assistant", "content": response["reply"],
                               **{k: v for k, v in response.items() if k not in ("reply", "author")}})
    if response.get("news_id"):
        memory["last_news_id"] = response["news_id"]
    try:
        storage.save_memory(memory)
    except Exception as e:
        logger.warning(f"Failed to save chat memory: {e}")
    return response


async def _handle_chat(req: AlfredChatRequest, history: List[Dict[str, str]], last_news_id: Optional[str]) -> Dict[str, Any]:
    msg_lower = req.message.lower().strip()

    # Attached file: answer the request about that file's content
    if req.attachment_text:
        prompt = (
            f"[Attached file: {req.attachment_name or 'file'}]\n{req.attachment_text[:ATTACHMENT_MAX_CHARS]}\n[End of file]\n\n"
            f"User message from Master Ashok: \"{req.message}\"\n\n"
            "Answer using the attached file. Respond as Alfred in character:"
        )
        try:
            reply = await _llm_client.generate_text(prompt, system_prompt=ALFRED_SYSTEM_PROMPT, history=history)
            return {"reply": reply.strip(), "author": "Alfred"}
        except Exception as e:
            logger.error(f"Attachment chat error: {e}")
            return {"reply": f"I could not read that file properly, Master Ashok: {e}", "author": "Alfred"}

    # 0. Intent: personal Google data (Drive / Calendar / Gmail) via Gemini tool calling
    if is_workspace_query(msg_lower):
        if not google_auth.is_connected():
            return {"reply": "Please connect your Google account first (the **Connect Google** button at the top), Master Ashok.",
                    "author": "Alfred"}
        try:
            progress.report("Looking through your Google account (Gmail, Drive, Calendar)...")
            return {"author": "Alfred", **await run_workspace_agent(req.message, history)}
        except Exception as e:
            logger.error(f"Workspace agent error: {e}")
            return {"reply": f"I could not reach your Google data: {e}", "author": "Alfred"}

    # 1. Intent: Scan News / Today News / Breaking News
    scan_triggers = [
        "scan news", "today news", "today's news", "get me the today news",
        "get me today news", "get today news", "latest news", "breaking news",
        "sweep news", "sweep feeds", "show news", "ai news", "what is the news today",
        "give me today news", "give me the news", "tell me the news", "tell me today news"
    ]
    if any(trigger in msg_lower for trigger in scan_triggers):
        try:
            if not _cached_news or "scan" in msg_lower or "sweep" in msg_lower:
                progress.report("Starting a fresh scan of AI news from the last 24 hours...")
                workflow_res = await run_research_workflow()
                for it in workflow_res.items:
                    _cached_news[it.id] = it
                _save_news_cache()
        except Exception as err:
            logger.warning(f"Live scan fallback to cache: {err}")
            progress.report("Live scan had a problem, using the most recent saved news instead...")

        progress.report("Writing your news digest...")
        top_items = _ranked_news()[:4]

        digest_blocks = [
            f"Here is your executive AI intelligence digest for today, Master Ashok. We have synthesized {len(top_items)} high-priority breakthroughs strictly from the last 24 hours:\n"
        ]
        for i, item in enumerate(top_items, 1):
            score = round(item.scores.total) if getattr(item, "scores", None) else 95
            item_lines = [
                f"**{i}. {item.title}** [Impact: {score}/100]",
                f"*Category: {item.category}*",
                f"{item.summary}"
            ]
            if item.key_bullet_points:
                item_lines.append("• " + "\n• ".join(item.key_bullet_points[:2]))
            item_lines.append(f"💡 **Why it matters:** {item.why_it_matters}")
            digest_blocks.append("\n".join(item_lines))

        full_reply = "\n\n---\n\n".join(digest_blocks)
        full_reply += "\n\n---\n\n**Generate images or cloned-videos:**"

        return {
            "reply": full_reply,
            "author": "Alfred",
            "action": "news_digest",
            "items": [{"id": it.id, "title": it.title} for it in top_items],
        }

    # Helper: Find referenced story (by number 1, 2, 3 or title keywords)
    def find_target_news():
        items = _ranked_news()
        if not items:
            return None
        padded = f" {msg_lower} "
        for idx in range(1, len(items) + 1):
            patterns = [f"news {idx}", f"story {idx}", f"number {idx}", f"item {idx}", f"#{idx}", f"for {idx}", f" {idx} "]
            if any(p in padded for p in patterns) or msg_lower == str(idx):
                return items[idx - 1]
        for it in items:
            words = [w for w in re.findall(r"\w+", it.title.lower()) if len(w) > 3]
            if any(w in msg_lower for w in words):
                return it
        # Fall back to the explicit context, then to the story discussed last (memory)
        for nid in (req.context_news_id, last_news_id):
            if nid and nid in _cached_news:
                return _cached_news[nid]
        return items[0]

    # 2. Intent: Generate Carousel / Images / Slides
    carousel_triggers = ["carousel", "image", "images", "slide", "slides", "photos", "infographic"]
    if any(t in msg_lower for t in carousel_triggers) and any(w in msg_lower for w in ["generate", "create", "make", "show", "give", "build", "craft", "for", "news"]):
        target = find_target_news()
        if target:
            progress.report(f"Picked the story: \"{target.title[:70]}\"")
        if target and ("single" in msg_lower or "linkedin" in msg_lower):
            brand = "linkedin" if "linkedin" in msg_lower else "instagram"
            await _carousel_generator.generate_single_async(target, brand)
            where = "LinkedIn (Ashokachari P)" if brand == "linkedin" else "Instagram (@ashok_a.i)"
            return {
                "reply": f"Master Ashok, here is your single {where} image for **\"{target.title}\"**.",
                "author": "Alfred",
                "action": "carousel",
                "format": "single",
                "brand": brand,
                "news_id": target.id,
                "title": target.title,
                "slides": [f"/api/v1/carousel/{target.id}/single?brand={brand}&v={int(time.time())}"],
                "caption": await _generate_post_caption(target),
            }
        if target:
            paths = await _carousel_generator.generate_carousel_async(target)
            slide_urls = [f"/api/v1/carousel/{target.id}/slide/{i}" for i in range(1, len(paths) + 1)]
            caption = await _generate_post_caption(target)
            return {
                "reply": f"Master Ashok, here is your {len(paths)}-image Instagram post for **\"{target.title}\"**.",
                "author": "Alfred",
                "action": "carousel",
                "news_id": target.id,
                "title": target.title,
                "slides": slide_urls,
                "caption": caption,
            }

    # 3. Intent: Generate Video Script / Blueprint
    video_triggers = ["video", "script", "blueprint", "reel", "short", "tiktok", "voiceover"]
    if any(t in msg_lower for t in video_triggers) and any(w in msg_lower for w in ["generate", "create", "make", "draft", "write", "show", "give", "for", "news"]):
        target = find_target_news()
        if target:
            progress.report(f"Writing a 35-40s video script with hook and scenes for \"{target.title[:60]}\"...")
            bp = await _content_agent.generate_blueprint(target)
            scenes_text = [
                f"**Scene {sc.scene_number} ({sc.timestamp}):**\n*Visual: {sc.visual_description}*\n\"{sc.spoken_script}\"\n[Popup: {sc.on_screen_text}]"
                for sc in bp.scenes
            ]
            reply_text = (
                f"Master Ashok, here is your 35-40s video script blueprint for **\"{target.title}\"**:\n\n"
                f"🎯 **Hook:** \"{bp.hook}\"\n\n"
                + "\n\n".join(scenes_text) + "\n\n"
                f"⚡ **Estimated Duration:** {bp.estimated_duration_seconds}s"
            )
            return {
                "reply": reply_text,
                "author": "Alfred",
                "action": "video_script",
                "news_id": target.id,
                "title": target.title,
            }

    # 4. General conversational query with persistent memory
    context_lines = []
    ranked = _ranked_news()[:4]
    if ranked:
        context_lines.append("[Current news digest]")
        context_lines += [f"{i}. {it.title} — {it.summary}" for i, it in enumerate(ranked, 1)]
    focus_id = req.context_news_id or last_news_id
    item = _cached_news.get(focus_id) if focus_id else None
    if item:
        context_lines.append(f"\n[Story currently in focus]\nTitle: {item.title}\nCategory: {item.category}\nSummary: {item.summary}\nWhy it matters: {item.why_it_matters}")

    full_prompt = "\n".join(context_lines) + f"\n\nUser message from Master Ashok: \"{req.message}\"\n\nRespond as Alfred in character:"
    try:
        progress.report("Thinking about your question...")
        reply = await _llm_client.generate_text(full_prompt, system_prompt=ALFRED_SYSTEM_PROMPT, history=history)
        return {"reply": reply.strip(), "author": "Alfred"}
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
        f"1. 🔬 **The Core Architecture:** How this system works in plain English.\n"
        f"2. 💼 **Strategic Advantage:** Why this matters for builders, creators, and enterprise.\n"
        f"3. 🎯 **Alfred's Recommendation:** How Master Ashok should angle this for social engagement."
    )
    try:
        reply = await _llm_client.generate_text(prompt, system_prompt=ALFRED_SYSTEM_PROMPT)
        return {"title": item.title, "briefing": reply.strip(), "author": "Alfred"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# Static Web App Mount
# ==============================================================================

mimetypes.add_type("application/manifest+json", ".webmanifest")

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
