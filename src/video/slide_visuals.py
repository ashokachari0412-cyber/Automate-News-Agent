"""Story-specific post visuals and overlay copy.

Gemini art-directs 4 realistic, real-world photo prompts plus short overlay copy
(one per carousel image) from the news content. Each prompt is rendered with the
Gemini image model; if that model is unavailable (e.g. free-tier key with no image
quota), Pollinations renders the same prompt.

Results are cached per story *content* (not news id, since ids like news_1 are
reused across scans).
"""

import asyncio
import hashlib
import json
import logging
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from config.settings import settings
from src import progress
from src.llm.client import LLMClient
from src.models.news import PrioritizedNewsItem

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
VISUALS_CACHE_DIR = BASE_DIR / "artifacts" / "visuals"

POST_ROLES = [
    "COVER: the scroll-stopping first image showing the real subject of the story",
    "WHAT'S NEW: what the product/update actually does, shown in a real setting",
    "KEY TAKEAWAYS: the concrete use case or capability in action",
    "WHY IT MATTERS: the concrete result or change the story brings (shown through the thing itself, not people reacting)",
]

# Distinct real-world shot types. Each carousel uses 4 different ones (rotated per story),
# so images never repeat the same "person at a laptop" scene.
SHOT_TYPES = [
    "PRODUCT HERO: the actual device, product, model or hardware from the story as the clear hero, studio lighting, clean backdrop",
    "MECHANISM: a clear physical visual of HOW the technology works (e.g. memory -> an organised archive of linked index cards; "
    "coding agent -> an editor window with blurred code and a terminal; chip -> silicon wafer), no people",
    "REAL PLACE: the real place where this story happens (lab, data center aisle, server room, factory, hospital, studio), no people",
    "DETAIL MACRO: an extreme close-up of the most relevant physical detail (a chip, a sensor, a screen edge, a robot hand, a cable)",
    "IN-USE CLOSE-UP: only hands using the product/tool from the story (phone, laptop, robot, camera), face not visible",
    "WIDE ENVIRONMENT: a wide establishing shot of the industry or setting the story is about, no people in focus",
    "RESULT: the concrete output the story produces (generated image on a monitor, a built app on a phone, a robot finishing a task)",
    "SYMBOLIC OBJECT: one striking real object that directly represents the story's core idea, minimal background",
]

PALETTES = [
    "warm golden-hour oranges", "cool teal and blue", "bright high-key white and pastel",
    "moody deep green", "bold red and black", "soft purple dusk", "sunny yellow and sky blue",
]

ART_DIRECTOR_PROMPT = """You are the creative director of a top AI-news Instagram creator page.
Plan a 4-image Instagram carousel about this story. Each image is a realistic photo with
short bold text overlaid at the bottom, like popular tech creators post.

Story title: {title}
Category: {category}
Summary: {summary}
Key points: {points}
Why it matters: {why}

Images (each one MUST use its assigned shot type and colour palette):
{roles}

For each image return:
- "prompt": a photo prompt (40-70 words) for a photorealistic editorial photograph, as if shot for
  Wired, Bloomberg or The Verge: real objects, hardware, screens, places, natural or practical lighting,
  35mm or 50mm lens, shallow depth of field.
  RELEVANCE IS THE TOP PRIORITY: a viewer must understand what the story is about from the photo alone.
  First decide WHAT the story is about (a model, a coding tool, a memory feature, a chip, a robot...),
  then show THAT thing or how it works, not people reacting to it.
  Name the concrete subject explicitly in the prompt (the actual product type, device, mechanism or place).
  NO PEOPLE by default. People (or just hands) may appear in at most ONE image, and only if the story is
  about people, jobs or a product held in the hand. Never "a person/developer/team at a laptop".
  NO unrelated props (no chess pieces, headphones, sticky notes, coffee cups, plants, typewriters).
  The 4 images must look different from each other: different subject, camera distance and angle.
  NEVER: holograms, neon glow, glowing circuits, sci-fi, futuristic cities, digital brains,
  abstract data streams, 3D renders, illustrations, cartoons.
  Vertical 4:5 composition with the main subject in the upper two-thirds (bottom third is for text).
  No text, letters, logos or readable screens in the image.
- "headline": punchy overlay headline, max 8 words, no emojis, no hashtags.
- "text": supporting overlay text, max 30 words, plain sentences, no emojis.
  For KEY TAKEAWAYS use 3 short lines separated by "\\n", each max 9 words.

Return JSON: {{"posts": [{{"prompt": "...", "headline": "...", "text": "..."}}, ... 4 items]}}"""

IMAGE_SUFFIX = (
    ", photorealistic real-world photograph, ultra sharp, crisp fine detail, high resolution, "
    "professional DSLR, 50mm, editorial photography, clean composition, no blur, no noise, "
    "no text, no watermark, no generic office workers at laptops"
)

# Per-model skip-until times: a model out of quota is skipped for a while instead of retried on
# every image (keeps generation fast while still picking quota back up later).
_model_cooldown_until: Dict[str, float] = {}
_QUOTA_COOLDOWN = 15 * 60

# The anonymous Pollinations tier is rate limited (one request at a time, roughly one
# every ~15s); faster calls are rejected with HTTP 402. Requests are serialized and spaced.
_pollinations_lock = threading.Lock()
_POLLINATIONS_MIN_INTERVAL = 15.0
_last_pollinations_call = 0.0


PLAN_VERSION = "v5"  # bump to invalidate cached plans/photos when the art direction changes


def _story_key(item: PrioritizedNewsItem) -> str:
    return hashlib.md5(f"{PLAN_VERSION}|{item.title}|{item.summary}".encode("utf-8")).hexdigest()[:16]


def _assign_shots(key: str) -> List[str]:
    """Picks 4 distinct shot types + palettes, rotated by story so carousels differ too."""
    n = int(key[:8], 16)
    shots = [SHOT_TYPES[(n + i * 3) % len(SHOT_TYPES)] for i in range(4)]
    palettes = [PALETTES[(n + i * 2) % len(PALETTES)] for i in range(4)]
    return [f"{role}\n   Shot type: {shot}\n   Colour palette: {pal}"
            for role, shot, pal in zip(POST_ROLES, shots, palettes)]


def _fallback_posts(item: PrioritizedNewsItem) -> List[Dict[str, str]]:
    """Used when Gemini can't plan the carousel: subject-first scenes, no generic people-at-laptops."""
    points = list(item.key_bullet_points or [])[:3]
    subject = f"{item.title} ({item.category})"
    return [
        {"prompt": f"Editorial product-style photo that clearly represents: {subject}. The technology itself as the hero, "
                   "dramatic studio lighting, clean dark backdrop, no people",
         "headline": item.title, "text": item.hook or item.summary[:160]},
        {"prompt": f"Close-up photo showing how this works: {item.summary[:200]}. Real hardware or a monitor with "
                   "blurred unreadable interface, shallow depth of field, no people",
         "headline": "What's new", "text": item.summary[:180]},
        {"prompt": f"Extreme macro photo of the most relevant physical detail of {subject}, such as a chip, "
                   "a screen edge or a circuit board, crisp detail, no people",
         "headline": "Key takeaways", "text": "\n".join(points)},
        {"prompt": f"Wide photo of the real place where {subject} is used, such as a modern server room or lab, "
                   "natural light, no people",
         "headline": "Why it matters", "text": item.why_it_matters[:180]},
    ]


async def _plan_posts(item: PrioritizedNewsItem, llm: LLMClient) -> List[Dict[str, str]]:
    prompt = ART_DIRECTOR_PROMPT.format(
        title=item.title,
        category=item.category,
        summary=item.summary,
        points=" | ".join(item.key_bullet_points or []),
        why=item.why_it_matters,
        roles="\n".join(f"{i}. {r}" for i, r in enumerate(_assign_shots(_story_key(item)), 1)),
    )
    fallback = _fallback_posts(item)
    try:
        data = await llm.generate_json(prompt)
        posts = [p for p in (data.get("posts") or []) if isinstance(p, dict) and p.get("prompt")]
        if len(posts) >= 4:
            return [
                {
                    "prompt": str(p["prompt"]),
                    "headline": str(p.get("headline") or fb["headline"]),
                    "text": str(p.get("text") or fb["text"]),
                }
                for p, fb in zip(posts[:4], fallback)
            ]
    except Exception as e:
        logger.warning(f"Post planning failed: {e}")
    return fallback


FRIENDLY_MODEL_NAMES = {
    "gemini-3-pro-image": "Nano Banana Pro",
    "gemini-nano-banana-2.1": "Nano Banana 2.1",
    "gemini-3.1-flash-image": "Nano Banana 2",
    "gemini-3.1-flash-lite-image": "Nano Banana 2 Lite",
    "gemini-2.5-flash-image": "Nano Banana",
}


def _image_models() -> List[str]:
    models = [m.strip() for m in (settings.GEMINI_IMAGE_MODELS or "").split(",") if m.strip()]
    return models or [settings.GEMINI_IMAGE_MODEL]


ASPECT_SIZES = {"4:5": (1080, 1350), "1:1": (1080, 1080), "9:16": (1080, 1920), "16:9": (1920, 1080)}


# GPT Image sizes per aspect (custom WIDTHxHEIGHT is accepted; the standard size is the fallback).
OPENAI_SIZES = {"16:9": ("1536x864", "1536x1024"), "9:16": ("864x1536", "1024x1536"),
                "4:5": ("1024x1280", "1024x1536"), "1:1": ("1024x1024", "1024x1024")}
_openai_cooldown_until = 0.0


def _openai_env() -> Dict[str, str]:
    """GPT Image settings, read fresh from .env (adding the key needs no restart)."""
    values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(env_file).items()}
    except Exception:
        pass
    return {"key": values.get("OPENAI_API_KEY", ""),
            "model": values.get("OPENAI_IMAGE_MODEL") or "gpt-image-2.5-flare",
            "quality": values.get("OPENAI_IMAGE_QUALITY") or "medium",
            "wavespeed_key": values.get("WAVESPEED_API_KEY", ""),
            "wavespeed_model": values.get("WAVESPEED_IMAGE_MODEL") or "bytedance/seedream-v5.0-flash",
            "wavespeed_resolution": values.get("WAVESPEED_IMAGE_RESOLUTION") or "2k"}


_wavespeed_cooldown_until = 0.0


def _render_with_wavespeed(prompt: str, label: str = "photo", aspect: str = "4:5") -> Optional[bytes]:
    """First choice for every image: Seedream 5.0 Flash on WaveSpeed (same key as Wan; ~$0.027 per image).
    None = fall back to GPT Image / Nano Banana / Flux."""
    global _wavespeed_cooldown_until
    cfg = _openai_env()
    if not cfg["wavespeed_key"] or time.monotonic() < _wavespeed_cooldown_until:
        return None
    name = cfg["wavespeed_model"].split("/")[-1]
    progress.report(f"Creating {label} with {name} (WaveSpeed)...")
    auth = {"Authorization": f"Bearer {cfg['wavespeed_key']}"}
    body = {"prompt": prompt[:4000], "aspect_ratio": aspect if aspect in ("1:1", "4:5", "9:16", "16:9") else "4:5",
            "resolution": cfg["wavespeed_resolution"], "output_format": "png"}
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            r = client.post(f"https://api.wavespeed.ai/api/v3/{cfg['wavespeed_model']}", headers=auth, json=body)
            data = r.json()
            data = data.get("data", data) if isinstance(data, dict) else {}
            job_id = data.get("id")
            if r.status_code != 200 or not job_id:
                if r.status_code in (401, 402, 403, 429) or "balance" in r.text.lower():
                    _wavespeed_cooldown_until = time.monotonic() + _QUOTA_COOLDOWN
                logger.warning(f"WaveSpeed image unavailable ({r.status_code}: {r.text[:200]}); falling back")
                return None
            for i in range(90):  # ~3 minutes
                time.sleep(2)
                res = client.get(f"https://api.wavespeed.ai/api/v3/predictions/{job_id}/result", headers=auth).json()
                res = res.get("data", res)
                status = str(res.get("status", "")).lower()
                if status == "completed":
                    outputs = res.get("outputs") or []
                    url = outputs[0] if isinstance(outputs, list) and outputs else outputs
                    if isinstance(url, dict):
                        url = url.get("url") or next(iter(url.values()), None)
                    if not url:
                        return None
                    img = client.get(url)
                    if img.status_code == 200 and len(img.content) > 5000:
                        logger.info(f"Image rendered with {cfg['wavespeed_model']} ({aspect}, {cfg['wavespeed_resolution']})")
                        return img.content
                    return None
                if status in ("failed", "cancelled", "timeout", "deleted"):
                    logger.warning(f"WaveSpeed image job {status}: {res.get('error')}; falling back")
                    return None
    except Exception as e:
        logger.warning(f"WaveSpeed image request failed ({e}); falling back")
    return None


def _render_with_openai(prompt: str, label: str = "photo", aspect: str = "4:5") -> Optional[bytes]:
    """First choice for every image: OpenAI GPT Image (medium quality). None = fall back to Gemini / Flux."""
    global _openai_cooldown_until
    cfg = _openai_env()
    if not cfg["key"] or time.monotonic() < _openai_cooldown_until:
        return None
    import base64
    progress.report(f"Creating {label} with GPT Image ({cfg['quality']})...")
    custom, standard = OPENAI_SIZES.get(aspect, ("1024x1024", "1024x1024"))
    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
    for size in dict.fromkeys([custom, standard]):
        body = {"model": cfg["model"], "prompt": prompt[:30000], "size": size, "quality": cfg["quality"],
                "output_format": "png", "n": 1}
        try:
            r = httpx.post("https://api.openai.com/v1/images/generations", headers=headers, json=body, timeout=180)
        except Exception as e:
            logger.warning(f"GPT Image request failed ({e}); falling back")
            return None
        if r.status_code == 200:
            data = (r.json().get("data") or [{}])[0].get("b64_json")
            if data:
                logger.info(f"Image rendered with {cfg['model']} ({size}, {cfg['quality']})")
                return base64.b64decode(data)
        msg = r.text[:200]
        if r.status_code == 400 and "size" in msg.lower() and size != standard:
            continue  # custom size not accepted: try the standard one
        if r.status_code in (401, 403, 429) or "billing" in msg.lower() or "quota" in msg.lower():
            _openai_cooldown_until = time.monotonic() + _QUOTA_COOLDOWN  # key/billing/quota problem: skip for a while
        logger.warning(f"GPT Image unavailable ({r.status_code}: {msg}); falling back to Gemini / Flux")
        return None
    return None


def _render_with_gemini(prompt: str, llm: LLMClient, label: str = "photo", aspect: str = "4:5") -> Optional[bytes]:
    """Tries the Nano Banana models best-first; a model that fails is skipped for a cooldown."""
    if not llm._client:
        return None
    from google.genai import types
    for model in _image_models():
        if time.monotonic() < _model_cooldown_until.get(model, 0):
            continue
        progress.report(f"Creating {label} with {FRIENDLY_MODEL_NAMES.get(model, model)}...")
        try:
            response = llm._client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=aspect),
                ),
            )
            for part in response.candidates[0].content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    logger.info(f"Image rendered with {model}")
                    return part.inline_data.data
            logger.warning(f"{model} returned no image, trying next model")
        except Exception as e:
            msg = str(e)
            if "NOT_FOUND" in msg or "PERMISSION_DENIED" in msg:
                _model_cooldown_until[model] = float("inf")  # not available to this key at all
            elif "RESOURCE_EXHAUSTED" in msg:
                _model_cooldown_until[model] = time.monotonic() + _QUOTA_COOLDOWN  # quota: retry later
            logger.warning(f"{model} unavailable ({msg[:100]}...), trying next model")
    return None


def _render_with_pollinations(prompt: str, seed: int, label: str = "photo", aspect: str = "4:5") -> Optional[bytes]:
    global _last_pollinations_call
    w, h = ASPECT_SIZES.get(aspect, (1080, 1350))
    url = (
        "https://image.pollinations.ai/prompt/"
        f"{urllib.parse.quote(prompt)}?width={w}&height={h}&nologo=true&model=flux&seed={seed}"
    )
    progress.report(f"Nano Banana isn't available, so {label} is queued on the free Flux image service...")
    with _pollinations_lock:
        for attempt in range(5):
            wait = _last_pollinations_call + _POLLINATIONS_MIN_INTERVAL * (attempt + 1) - time.monotonic()
            if wait > 0:
                progress.report(f"Waiting about {int(wait)}s for the free image service before {label}...")
                time.sleep(wait)
            progress.report(f"Creating {label} with Flux (free image service)..."
                            if attempt == 0 else f"Retrying {label} (attempt {attempt + 1} of 5)...")
            try:
                with httpx.Client(timeout=90.0, follow_redirects=True) as client:
                    res = client.get(url)
                if res.status_code == 200 and len(res.content) > 5000:
                    return res.content
                logger.warning(f"Pollinations returned HTTP {res.status_code} (attempt {attempt + 1}/5)")
            except Exception as e:
                logger.warning(f"Pollinations render failed: {e}")
            finally:
                _last_pollinations_call = time.monotonic()
    return None


def _render(prompt: str, seed: int, out_path: Path, llm: LLMClient,
            label: str = "photo", on_done=None, aspect: str = "4:5") -> Optional[Path]:
    full_prompt = prompt + IMAGE_SUFFIX
    data = (_render_with_wavespeed(full_prompt, label, aspect)         # 1. Seedream 5.0 Flash (WaveSpeed)
            or _render_with_openai(full_prompt, label, aspect)           # 2. GPT Image (only if OPENAI_API_KEY set)
            or _render_with_gemini(full_prompt, llm, label, aspect)      # 3. Nano Banana (free tier)
            or _render_with_pollinations(full_prompt, seed, label, aspect))  # 4. Flux (free)
    if on_done:
        on_done(bool(data))
    if not data:
        return None
    out_path.write_bytes(data)
    return out_path


async def prepare_post_assets(item: PrioritizedNewsItem, llm: Optional[LLMClient] = None,
                              count: int = 4) -> Tuple[List[Dict[str, str]], List[Optional[Path]]]:
    """Returns (4 post plans with headline/text, photo paths or None where rendering failed).

    Only the first `count` photos are rendered (the single LinkedIn image needs just the cover).
    """
    key = _story_key(item)
    story_dir = VISUALS_CACHE_DIR / key
    story_dir.mkdir(parents=True, exist_ok=True)
    paths = [story_dir / f"photo_{i}.jpg" for i in range(1, 5)]

    llm = llm or LLMClient(settings)
    plan_file = story_dir / "plan.json"
    if plan_file.exists():
        progress.report("Reusing the photo plan already made for this story...")
        posts = json.loads(plan_file.read_text(encoding="utf-8"))
    else:
        progress.report("Reading the story and planning relevant photos and headlines...")
        posts = await _plan_posts(item, llm)
        plan_file.write_text(json.dumps(posts, indent=2, ensure_ascii=False), encoding="utf-8")

    def cached(p: Path) -> bool:
        return p.exists() and p.stat().st_size > 5000

    todo = [i for i, path in enumerate(paths[:count]) if not cached(path)]
    if not todo:
        progress.report("Photos for this story are already made, reusing them...")
    done = {"n": 0}
    lock = threading.Lock()

    def on_done(ok: bool) -> None:
        with lock:
            done["n"] += 1
            if len(todo) > 1:
                progress.report(f"{done['n']} of {len(todo)} photos ready...")

    base_seed = int(key[:6], 16)
    tasks = [
        asyncio.sleep(0, result=path) if i not in todo
        else asyncio.to_thread(_render, post["prompt"], base_seed + i, path, llm,
                               f"photo {i + 1} of {count}" if count > 1 else "the photo", on_done)
        for i, (post, path) in enumerate(zip(posts[:count], paths[:count]))
    ]
    return posts, list(await asyncio.gather(*tasks))
