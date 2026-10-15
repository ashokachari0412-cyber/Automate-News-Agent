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
from src.llm.client import LLMClient
from src.models.news import PrioritizedNewsItem

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
VISUALS_CACHE_DIR = BASE_DIR / "artifacts" / "visuals"

POST_ROLES = [
    "COVER: the scroll-stopping first image showing the real subject of the story",
    "WHAT'S NEW: what the product/update actually does, shown in a real setting",
    "KEY TAKEAWAYS: the concrete use case or capability in action",
    "WHY IT MATTERS: the real-world impact on people, teams or an industry",
]

# Distinct real-world shot types. Each carousel uses 4 different ones (rotated per story),
# so images never repeat the same "person at a laptop" scene.
SHOT_TYPES = [
    "CONCEPTUAL STILL LIFE: a striking real-object metaphor for the idea (e.g. memory -> a wall of sticky notes and old photo prints), studio product lighting, bold solid-colour background",
    "MACRO CLOSE-UP: extreme close-up of a real detail (hands, a keyboard key, a chip, an eye, a camera lens), very shallow depth of field",
    "TOP-DOWN FLAT LAY: overhead shot of real objects arranged neatly on a coloured surface that tell the story",
    "OUTDOOR STREET / LIFESTYLE: candid shot outside (street, cafe, train, park, rooftop) showing everyday life touched by the story",
    "DRAMATIC HARDWARE / OBJECT HERO: a single real device or object as the hero, low angle, rim light, dark backdrop",
    "WIDE ENVIRONMENT: a wide establishing shot of a real place tied to the story (factory floor, hospital, classroom, data center aisle, film set)",
    "SPLIT / BEFORE-AFTER: a real photographic diptych-like composition contrasting the old way and the new way",
    "PORTRAIT WITH EMOTION: an expressive close portrait of one person reacting (surprise, relief, focus), coloured gel lighting",
    "AERIAL / DRONE: a top-down aerial photo of a city, crowd, traffic or landscape as a metaphor for scale",
    "VINTAGE / ANALOG METAPHOR: real retro objects (typewriter, filing cabinets, film camera, rotary phone) contrasting with the new tech",
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
- "prompt": a photo prompt (40-70 words) for a REAL-WORLD, photorealistic editorial photograph,
  as if shot by a professional photographer for Wired, Bloomberg or The Verge: real people, real
  offices, real laptops/phones/screens/hardware, real places, natural or practical lighting,
  35mm or 50mm lens, shallow depth of field, candid and authentic.
  Be specific to THIS story's subject. The 4 images must look completely different from each other:
  different location, subject, camera distance, angle and colours.
  At most ONE image may show a person sitting at a desk/computer; prefer objects, places and metaphors.
  When people appear, vary age, gender and ethnicity.
  NEVER: holograms, neon glow, glowing circuits, sci-fi, futuristic cities, digital brains,
  abstract data streams, 3D renders, illustrations, cartoons.
  Vertical 4:5 composition with the main subject in the upper two-thirds (bottom third is for text).
  No text, letters, logos or readable screens in the image.
- "headline": punchy overlay headline, max 8 words, no emojis, no hashtags.
- "text": supporting overlay text, max 30 words, plain sentences, no emojis.
  For KEY TAKEAWAYS use 3 short lines separated by "\\n", each max 9 words.

Return JSON: {{"posts": [{{"prompt": "...", "headline": "...", "text": "..."}}, ... 4 items]}}"""

IMAGE_SUFFIX = (
    ", photorealistic real-world photograph, natural lighting, 35mm, editorial photography, "
    "authentic, sharp focus, no text, no watermark"
)

# Set once the Gemini image model refuses (quota/billing) so we don't retry it on every image.
_gemini_image_disabled = False

# The anonymous Pollinations tier is rate limited (one request at a time, roughly one
# every ~15s); faster calls are rejected with HTTP 402. Requests are serialized and spaced.
_pollinations_lock = threading.Lock()
_POLLINATIONS_MIN_INTERVAL = 15.0
_last_pollinations_call = 0.0


PLAN_VERSION = "v3"  # bump to invalidate cached plans/photos when the art direction changes


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
    points = list(item.key_bullet_points or [])[:3]
    subject = item.title
    return [
        {"prompt": f"Candid photo of a software developer at a desk with a laptop, reacting to news about {subject}",
         "headline": item.title, "text": item.hook or item.summary[:160]},
        {"prompt": f"Over-the-shoulder photo of a person using a laptop in a bright modern office, working with {subject}",
         "headline": "What's new", "text": item.summary[:180]},
        {"prompt": f"Small team collaborating around a laptop in a real startup office, discussing {subject}",
         "headline": "Key takeaways", "text": "\n".join(points)},
        {"prompt": "Wide photo of a busy modern open-plan office with people working on computers, natural daylight",
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


def _render_with_gemini(prompt: str, llm: LLMClient) -> Optional[bytes]:
    global _gemini_image_disabled
    if _gemini_image_disabled or not llm._client:
        return None
    try:
        from google.genai import types
        response = llm._client.models.generate_content(
            model=settings.GEMINI_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="4:5"),
            ),
        )
        for part in response.candidates[0].content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "PERMISSION_DENIED" in msg or "NOT_FOUND" in msg:
            _gemini_image_disabled = True
            logger.warning(f"Gemini image model unavailable ({msg[:120]}...). Using fallback renderer.")
        else:
            logger.warning(f"Gemini image generation failed: {msg[:200]}")
    return None


def _render_with_pollinations(prompt: str, seed: int) -> Optional[bytes]:
    global _last_pollinations_call
    url = (
        "https://image.pollinations.ai/prompt/"
        f"{urllib.parse.quote(prompt)}?width=1080&height=1350&nologo=true&model=flux&seed={seed}"
    )
    with _pollinations_lock:
        for attempt in range(5):
            wait = _last_pollinations_call + _POLLINATIONS_MIN_INTERVAL * (attempt + 1) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
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


def _render(prompt: str, seed: int, out_path: Path, llm: LLMClient) -> Optional[Path]:
    full_prompt = prompt + IMAGE_SUFFIX
    data = _render_with_gemini(full_prompt, llm) or _render_with_pollinations(full_prompt, seed)
    if not data:
        return None
    out_path.write_bytes(data)
    return out_path


async def prepare_post_assets(item: PrioritizedNewsItem,
                              llm: Optional[LLMClient] = None) -> Tuple[List[Dict[str, str]], List[Optional[Path]]]:
    """Returns (4 post plans with headline/text, 4 photo paths or None where rendering failed)."""
    key = _story_key(item)
    story_dir = VISUALS_CACHE_DIR / key
    story_dir.mkdir(parents=True, exist_ok=True)
    paths = [story_dir / f"photo_{i}.jpg" for i in range(1, 5)]

    llm = llm or LLMClient(settings)
    plan_file = story_dir / "plan.json"
    if plan_file.exists():
        posts = json.loads(plan_file.read_text(encoding="utf-8"))
    else:
        posts = await _plan_posts(item, llm)
        plan_file.write_text(json.dumps(posts, indent=2, ensure_ascii=False), encoding="utf-8")

    def cached(p: Path) -> bool:
        return p.exists() and p.stat().st_size > 5000

    base_seed = int(key[:6], 16)
    tasks = [
        asyncio.sleep(0, result=path) if cached(path)
        else asyncio.to_thread(_render, post["prompt"], base_seed + i, path, llm)
        for i, (post, path) in enumerate(zip(posts, paths))
    ]
    return posts, list(await asyncio.gather(*tasks))
