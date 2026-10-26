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
  BRAND FIRST: if the story is about a named product, model or company (Claude, ChatGPT, Gemini, Llama, Grok,
  Copilot, Nvidia, Tesla...), image 1 MUST make that brand instantly recognizable: show its real logo in its
  real brand colours, e.g. on a laptop or phone screen, a sign, a sticker or as a large physical object.
  The image model may not know the logo, so DESCRIBE IT VISUALLY in the prompt (shape + colour), e.g.
  "the Claude logo, a coral-orange starburst of thin straight rays, next to the word 'Claude' in a clean
  serif font, on a warm cream background". Always put the brand NAME as clean on-screen text next to the
  logo: image models can't copy logos exactly, but the name makes the brand unmistakable.
  Tools built for or around a brand (a plugin, extension or open-source add-on for Claude, ChatGPT...) show
  the brand they extend. Use the brand in at least 2 of the 4 images.
  NEVER replace the product with a literal metaphor: no hard drive or memory chip for "memory", no brain
  for "AI", no robot for "agent", unless the story really is about that hardware.
  NO PEOPLE by default. People (or just hands) may appear in at most ONE image, and only if the story is
  about people, jobs or a product held in the hand. Never "a person/developer/team at a laptop".
  NO unrelated props (no chess pieces, headphones, sticky notes, coffee cups, plants, typewriters).
  The 4 images must look different from each other: different subject, camera distance and angle.
  NEVER: holograms, neon glow, glowing circuits, sci-fi, futuristic cities, digital brains,
  abstract data streams, 3D renders, illustrations, cartoons.
  Vertical 4:5 composition with the main subject in the upper two-thirds (bottom third is for text).
  The ONLY text allowed in an image is the brand or product name as part of its logo/screen; no other
  readable text, no made-up logos.
- "headline": punchy overlay headline, max 8 words, no emojis, no hashtags.
- "text": supporting overlay text, max 30 words, plain sentences, no emojis.
  For KEY TAKEAWAYS use 3 short lines separated by "\\n", each max 9 words.

Return JSON: {{"brand": "<the main product/company, or empty>",
 "posts": [{{"prompt": "...", "headline": "...", "text": "..."}}, ... 4 items]}}"""

IMAGE_SUFFIX = (
    ", photorealistic real-world photograph, ultra sharp, crisp fine detail, high resolution, "
    "professional DSLR, 50mm, editorial photography, clean composition, no blur, no noise, "
    "no watermark, no random or garbled text (a brand logo or name only where the prompt asks for it), "
    "no generic office workers at laptops"
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
        {"prompt": f"Editorial photo that clearly represents: {subject}. If it is a named product or company, show its "
                   "real logo in its brand colours on a laptop screen as the hero (not a metaphor), dramatic studio "
                   "lighting, clean dark backdrop, no people",
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


def _openai_env() -> Dict[str, str]:
    """WaveSpeed image settings, read fresh from .env (adding the key needs no restart)."""
    values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(env_file).items()}
    except Exception:
        pass
    return {"wavespeed_key": values.get("WAVESPEED_API_KEY", ""),
            "wavespeed_model": values.get("WAVESPEED_IMAGE_MODEL") or "bytedance/seedream-v5.0-flash",
            "wavespeed_resolution": values.get("WAVESPEED_IMAGE_RESOLUTION") or "2k"}


_wavespeed_cooldown_until = 0.0


def _render_with_wavespeed(prompt: str, label: str = "photo", aspect: str = "4:5") -> Optional[bytes]:
    """First choice for every image: Seedream 5.0 Flash on WaveSpeed (same key as Wan; ~$0.027 per image).
    None = fall back to Nano Banana / Flux."""
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
                progress.sleep(2)
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
                        from src import costs
                        costs.record("WaveSpeed", f"Image · {name} ({aspect})", costs.SEEDREAM_PER_IMAGE)
                        return img.content
                    return None
                if status in ("failed", "cancelled", "timeout", "deleted"):
                    logger.warning(f"WaveSpeed image job {status}: {res.get('error')}; falling back")
                    return None
    except Exception as e:
        logger.warning(f"WaveSpeed image request failed ({e}); falling back")
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
                    from src import costs
                    costs.record("Google Gemini", f"Image · {FRIENDLY_MODEL_NAMES.get(model, model)}", 0.0, "free tier")
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
                progress.sleep(wait)
            progress.report(f"Creating {label} with Flux (free image service)..."
                            if attempt == 0 else f"Retrying {label} (attempt {attempt + 1} of 5)...")
            try:
                with httpx.Client(timeout=90.0, follow_redirects=True) as client:
                    res = client.get(url)
                if res.status_code == 200 and len(res.content) > 5000:
                    from src import costs
                    costs.record("Pollinations", "Image · Flux", 0.0, "free")
                    return res.content
                logger.warning(f"Pollinations returned HTTP {res.status_code} (attempt {attempt + 1}/5)")
            except Exception as e:
                logger.warning(f"Pollinations render failed: {e}")
            finally:
                _last_pollinations_call = time.monotonic()
    return None


EDIT_ASPECTS = {"1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "9:16": 0.5625, "16:9": 1.7778, "4:3": 1.3333, "2:3": 0.6667}


def _nearest_aspect(path: Path) -> str:
    from PIL import Image
    w, h = Image.open(path).size
    return min(EDIT_ASPECTS, key=lambda k: abs(EDIT_ASPECTS[k] - w / h))


def _edit_with_wavespeed(src: Path, instruction: str, aspect: str) -> Optional[bytes]:
    """Seedream 5.0 Flash Edit on WaveSpeed: the image + his instruction -> the changed image ($0.027)."""
    cfg = _openai_env()
    if not cfg["wavespeed_key"]:
        return None
    import io
    from PIL import Image
    auth = {"Authorization": f"Bearer {cfg['wavespeed_key']}"}
    buf = io.BytesIO()
    Image.open(src).convert("RGB").save(buf, "JPEG", quality=95)
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            up = client.post("https://api.wavespeed.ai/api/v3/media/upload/binary", headers=auth,
                             files={"file": (f"{src.stem}.jpg", buf.getvalue(), "image/jpeg")})
            image_url = (up.json().get("data") or {}).get("download_url") if up.status_code == 200 else None
            if not image_url:
                logger.warning(f"WaveSpeed upload for edit failed ({up.status_code}): {up.text[:200]}")
                return None
            r = client.post("https://api.wavespeed.ai/api/v3/bytedance/seedream-v5.0-flash/edit", headers=auth, json={
                "prompt": instruction[:2000], "images": [image_url], "aspect_ratio": aspect,
                "resolution": "2k", "output_format": "png"})
            data = r.json()
            data = data.get("data", data) if isinstance(data, dict) else {}
            if r.status_code != 200 or not data.get("id"):
                logger.warning(f"Seedream edit unavailable ({r.status_code}: {r.text[:200]})")
                return None
            for _ in range(90):  # ~3 minutes
                progress.sleep(2)
                res = client.get(f"https://api.wavespeed.ai/api/v3/predictions/{data['id']}/result", headers=auth).json()
                res = res.get("data", res)
                status = str(res.get("status", "")).lower()
                if status == "completed":
                    outputs = res.get("outputs") or []
                    url = outputs[0] if isinstance(outputs, list) and outputs else None
                    img = client.get(url) if url else None
                    if img is not None and img.status_code == 200 and len(img.content) > 5000:
                        from src import costs
                        costs.record("WaveSpeed", f"Image edit · seedream-v5.0-flash ({aspect})", costs.SEEDREAM_PER_IMAGE)
                        return img.content
                    return None
                if status in ("failed", "cancelled", "timeout", "deleted"):
                    logger.warning(f"Seedream edit {status}: {res.get('error')}")
                    return None
    except Exception as e:
        logger.warning(f"Seedream edit request failed ({e})")
    return None


def _edit_with_gemini(src: Path, instruction: str, llm: LLMClient) -> Optional[bytes]:
    """Fallback: Nano Banana edits the image (free tier)."""
    if not llm._client:
        return None
    from PIL import Image
    from google.genai import types
    for model in _image_models():
        try:
            response = llm._client.models.generate_content(
                model=model, contents=[Image.open(src), instruction],
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
            for part in response.candidates[0].content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    from src import costs
                    costs.record("Google Gemini", f"Image edit · {FRIENDLY_MODEL_NAMES.get(model, model)}", 0.0, "free tier")
                    return part.inline_data.data
        except Exception as e:
            logger.warning(f"{model} edit unavailable ({str(e)[:100]})")
    return None


def edit_image(src: Path, instruction: str, out: Path, llm: Optional[LLMClient] = None,
               aspect: Optional[str] = None) -> Path:
    """Modify: his words go straight to the image model together with the current image; the rest of the
    picture is kept. Writes the result to `out` (the previous version is kept next to it as *.prev)."""
    aspect = aspect or _nearest_aspect(src)
    prompt = f"{instruction.strip()}. Keep everything else in the image the same."
    progress.report("Sending your change to Seedream (image edit)...")
    data = _edit_with_wavespeed(src, prompt, aspect)
    if not data:
        progress.report("Seedream edit unavailable, trying Nano Banana...")
        data = _edit_with_gemini(src, prompt, llm or LLMClient(settings))
    if not data:
        raise RuntimeError("The image models couldn't make that change right now. Please try again in a minute.")
    if out.exists():
        out.with_name(out.stem + ".prev" + out.suffix).write_bytes(out.read_bytes())
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(data))
    img.convert("RGB").save(out) if out.suffix.lower() in (".jpg", ".jpeg") else img.save(out)
    return out


def story_photo(item: PrioritizedNewsItem, index: int) -> Path:
    """Where the clean (no text) photo for post `index` (1-4) of a story is stored."""
    return VISUALS_CACHE_DIR / _story_key(item) / f"photo_{index}.jpg"


def _render(prompt: str, seed: int, out_path: Path, llm: LLMClient,
            label: str = "photo", on_done=None, aspect: str = "4:5") -> Optional[Path]:
    full_prompt = prompt + IMAGE_SUFFIX
    data = (_render_with_wavespeed(full_prompt, label, aspect)         # 1. Seedream 5.0 Flash (WaveSpeed)
            or _render_with_gemini(full_prompt, llm, label, aspect)      # 2. Nano Banana (free tier)
            or _render_with_pollinations(full_prompt, seed, label, aspect))  # 3. Flux (free)
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
