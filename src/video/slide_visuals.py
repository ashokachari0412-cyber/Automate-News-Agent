"""Story-specific post visuals and overlay copy.

Gemini art-directs 4 realistic, real-world photo prompts plus short overlay copy
(one per carousel image) from the news content. Every image is rendered (and edited)
by GPT Image 2.5 on WaveSpeed only - there are no fallback models.

Results are cached per story *content* (not news id, since ids like news_1 are
reused across scans).
"""

import asyncio
import hashlib
import json
import logging
import threading
import time
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

# Extra roles for 5-6 image carousels, and which roles each size uses (indexes into ALL_ROLES), in order.
ALL_ROLES = POST_ROLES + [
    "HOW IT WORKS: the mechanism behind it, shown as a clear real-world visual",
    "REAL-WORLD USE: who uses it and where, shown through the product or place itself",
]
ROLE_ORDER = {1: [0], 2: [0, 3], 3: [0, 1, 3], 4: [0, 1, 2, 3], 5: [0, 1, 4, 2, 3], 6: [0, 1, 4, 2, 5, 3]}
MAX_IMAGES = 6

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
Plan a {n}-image Instagram carousel about this story. Each image is a realistic photo with
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
  the brand they extend. Use the brand in at least half of the images.
  NEVER replace the product with a literal metaphor: no hard drive or memory chip for "memory", no brain
  for "AI", no robot for "agent", unless the story really is about that hardware.
  NO PEOPLE by default. Ordinary people (or just hands) may appear in at most ONE image, and only if the story is
  about people, jobs or a product held in the hand. Never "a person/developer/team at a laptop".
  FAMOUS PEOPLE (they stop the scroll, so use them - but only when it truly fits): show a well-known real
  person ONLY when the story is directly about them, or is a BIG company-level moment of the company they lead:
  a flagship model launch, funding or valuation, a big deal or acquisition, a keynote, a lawsuit, leadership news
  (e.g. Sam Altman for OpenAI, Elon Musk for xAI / Tesla / SpaceX, Sundar Pichai for Google, Jensen Huang for
  NVIDIA, Dario Amodei for Anthropic, Mark Zuckerberg for Meta, Satya Nadella for Microsoft).
  For a smaller product feature, a tool, research or general industry news, show NO famous people.
  When used: in at most 2 images (usually the cover), as a realistic editorial press photo (speaking on a
  keynote stage, a portrait at a launch event), neutral or positive. Never show them doing, saying or holding
  anything the story doesn't report, never with a rival's product, never in a fake, embarrassing or private scene.
  (This rule may replace the assigned shot type of the image that shows the person.)
  NO unrelated props (no chess pieces, headphones, sticky notes, coffee cups, plants, typewriters).
  The images must look different from each other: different subject, camera distance and angle.
  NEVER: holograms, neon glow, glowing circuits, sci-fi, futuristic cities, digital brains,
  abstract data streams, 3D renders, illustrations, cartoons.
  Vertical 4:5 composition with the main subject in the upper two-thirds (bottom third is for text).
  The ONLY text allowed in an image is the brand or product name as part of its logo/screen; no other
  readable text, no made-up logos.
- "headline": punchy overlay headline, max 8 words, no emojis, no hashtags.
- "text": supporting overlay text, max 30 words, plain sentences, no emojis.
  For KEY TAKEAWAYS use 3 short lines separated by "\\n", each max 9 words.

Return JSON: {{"brand": "<the main product/company, or empty>",
 "posts": [{{"prompt": "...", "headline": "...", "text": "..."}}, ... exactly {n} items]}}"""

IMAGE_SUFFIX = (
    ", photorealistic real-world photograph, ultra sharp, crisp fine detail, high resolution, "
    "professional DSLR, 50mm, editorial photography, clean composition, no blur, no noise, "
    "no watermark, no random or garbled text (a brand logo or name only where the prompt asks for it), "
    "no generic office workers at laptops"
)


PLAN_VERSION = "v5"  # bump to invalidate cached plans/photos when the art direction changes


def _story_key(item: PrioritizedNewsItem) -> str:
    return hashlib.md5(f"{PLAN_VERSION}|{item.title}|{item.summary}".encode("utf-8")).hexdigest()[:16]


def _roles(total: int) -> List[str]:
    return [ALL_ROLES[i] for i in ROLE_ORDER[total]]


def _assign_shots(key: str, total: int = 4) -> List[str]:
    """Picks `total` distinct shot types + palettes, rotated by story so carousels differ too."""
    n = int(key[:8], 16)
    shots = [SHOT_TYPES[(n + i * 3) % len(SHOT_TYPES)] for i in range(total)]
    palettes = [PALETTES[(n + i * 2) % len(PALETTES)] for i in range(total)]
    return [f"{role}\n   Shot type: {shot}\n   Colour palette: {pal}"
            for role, shot, pal in zip(_roles(total), shots, palettes)]


def _fallback_posts(item: PrioritizedNewsItem, total: int = 4) -> List[Dict[str, str]]:
    """Used when Gemini can't plan the carousel: subject-first scenes, no generic people-at-laptops."""
    points = list(item.key_bullet_points or [])[:3]
    subject = f"{item.title} ({item.category})"
    every = [
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
        {"prompt": f"Clear real-world visual of how {subject} works, real hardware or a monitor with blurred "
                   "unreadable interface, shallow depth of field, no people",
         "headline": "How it works", "text": item.summary[:180]},
        {"prompt": f"Photo of {subject} being used in a real setting, shown through the product or place itself, "
                   "natural light, no faces",
         "headline": "Real-world use", "text": (points[0] if points else item.summary[:160])},
    ]
    return [every[i] for i in ROLE_ORDER[total]]


async def _plan_posts(item: PrioritizedNewsItem, llm: LLMClient, total: int = 4) -> List[Dict[str, str]]:
    prompt = ART_DIRECTOR_PROMPT.format(
        n=total,
        title=item.title,
        category=item.category,
        summary=item.summary,
        points=" | ".join(item.key_bullet_points or []),
        why=item.why_it_matters,
        roles="\n".join(f"{i}. {r}" for i, r in enumerate(_assign_shots(_story_key(item), total), 1)),
    )
    fallback = _fallback_posts(item, total)
    try:
        data = await llm.generate_json(prompt)
        posts = [p for p in (data.get("posts") or []) if isinstance(p, dict) and p.get("prompt")]
        if len(posts) >= total:
            return [
                {
                    "prompt": str(p["prompt"]),
                    "headline": str(p.get("headline") or fb["headline"]),
                    "text": str(p.get("text") or fb["text"]),
                }
                for p, fb in zip(posts[:total], fallback)
            ]
    except Exception as e:
        logger.warning(f"Post planning failed: {e}")
    return fallback


ASPECTS = ("1:1", "1:2", "2:1", "1:3", "3:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "9:21", "21:9")


def _openai_env() -> Dict[str, str]:
    """GPT Image 2.5 on WaveSpeed, read fresh from .env (changing it needs no restart)."""
    values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(env_file).items()}
    except Exception:
        pass
    model = values.get("WAVESPEED_IMAGE_MODEL") or "openai/gpt-image-2.5-flare"
    model = model.removesuffix("/text-to-image").removesuffix("/edit")
    return {"wavespeed_key": values.get("WAVESPEED_API_KEY", ""),
            "wavespeed_model": model,
            "wavespeed_resolution": (values.get("WAVESPEED_IMAGE_RESOLUTION") or "2k").lower(),
            "wavespeed_quality": (values.get("WAVESPEED_IMAGE_QUALITY") or "medium").lower()}


def image_price(edit: bool = False) -> float:
    """USD per image for the configured quality + resolution."""
    from src import costs
    cfg = _openai_env()
    table = costs.GPT_IMAGE_EDIT if edit else costs.GPT_IMAGE
    row = table.get(cfg["wavespeed_quality"], table["medium"])
    return row.get(cfg["wavespeed_resolution"], row["2k"])


def _upload_to_wavespeed(client: httpx.Client, auth: Dict[str, str], src: Path) -> Optional[str]:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.open(src).convert("RGB").save(buf, "JPEG", quality=95)
    up = client.post("https://api.wavespeed.ai/api/v3/media/upload/binary", headers=auth,
                     files={"file": (f"{src.stem}.jpg", buf.getvalue(), "image/jpeg")})
    url = (up.json().get("data") or {}).get("download_url") if up.status_code == 200 else None
    if not url:
        logger.warning(f"WaveSpeed upload for edit failed ({up.status_code}): {up.text[:200]}")
    return url


def _wavespeed_job(endpoint: str, body: Dict, what: str, src: Optional[Path] = None) -> Optional[bytes]:
    """One GPT Image 2.5 job on WaveSpeed ("text-to-image", or "edit" with `src`). None = it failed (logged)."""
    cfg = _openai_env()
    if not cfg["wavespeed_key"]:
        logger.warning("WAVESPEED_API_KEY is missing in .env - can't create images")
        return None
    edit = src is not None
    auth = {"Authorization": f"Bearer {cfg['wavespeed_key']}"}
    body = {**body, "resolution": cfg["wavespeed_resolution"], "quality": cfg["wavespeed_quality"],
            "output_format": "png"}
    name = cfg["wavespeed_model"].split("/")[-1]
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            if edit:
                image_url = _upload_to_wavespeed(client, auth, src)
                if not image_url:
                    return None
                body["images"] = [image_url]
            # Never retry the submit: an unclear failure may still have created a billed job.
            r = client.post(f"https://api.wavespeed.ai/api/v3/{cfg['wavespeed_model']}/{endpoint}",
                            headers=auth, json=body)
            data = r.json() if r.content else {}
            data = data.get("data", data) if isinstance(data, dict) else {}
            job_id = data.get("id")
            if r.status_code != 200 or not job_id:
                logger.warning(f"{name} {what} unavailable ({r.status_code}: {r.text[:200]})")
                return None
            for _ in range(150):  # ~5 minutes
                progress.sleep(2)
                res = client.get(f"https://api.wavespeed.ai/api/v3/predictions/{job_id}/result", headers=auth)
                if res.status_code == 429 or res.status_code >= 500:
                    continue
                res = res.json()
                res = res.get("data", res)
                status = str(res.get("status", "")).lower()
                if status == "completed":
                    outputs = res.get("outputs") or []
                    url = outputs[0] if isinstance(outputs, list) and outputs else outputs
                    if isinstance(url, dict):
                        url = url.get("url") or next(iter(url.values()), None)
                    img = client.get(url) if url else None
                    if img is not None and img.status_code == 200 and len(img.content) > 5000:
                        aspect = body.get("aspect_ratio", "auto")
                        logger.info(f"Image {what} with {cfg['wavespeed_model']} ({aspect}, "
                                    f"{cfg['wavespeed_resolution']}, {cfg['wavespeed_quality']})")
                        from src import costs
                        costs.record("WaveSpeed", f"Image{' edit' if edit else ''} · {name} ({aspect}, "
                                     f"{cfg['wavespeed_quality']})", image_price(edit))
                        return img.content
                    return None
                if status in ("failed", "cancelled", "timeout", "deleted"):
                    logger.warning(f"{name} {what} job {status}: {res.get('error')}")
                    return None
            logger.warning(f"{name} {what} timed out")
    except Exception as e:
        logger.warning(f"{name} {what} request failed ({e})")
    return None


def _render_with_wavespeed(prompt: str, label: str = "photo", aspect: str = "4:5") -> Optional[bytes]:
    """The only image model: GPT Image 2.5 on WaveSpeed (no fallbacks)."""
    progress.report(f"Creating {label} with GPT Image 2.5...")
    return _wavespeed_job("text-to-image", {"prompt": prompt[:4000],
                                            "aspect_ratio": aspect if aspect in ASPECTS else "4:5"}, "rendered")


EDIT_ASPECTS = {"1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "9:16": 0.5625, "16:9": 1.7778, "4:3": 1.3333, "2:3": 0.6667}


def _nearest_aspect(path: Path) -> str:
    from PIL import Image
    w, h = Image.open(path).size
    return min(EDIT_ASPECTS, key=lambda k: abs(EDIT_ASPECTS[k] - w / h))


def edit_image(src: Path, instruction: str, out: Path, llm: Optional[LLMClient] = None,
               aspect: Optional[str] = None) -> Path:
    """Modify: his words go straight to GPT Image 2.5 together with the current image; the rest of the
    picture is kept. Writes the result to `out` (the previous version is kept next to it as *.prev)."""
    aspect = aspect or _nearest_aspect(src)
    prompt = f"{instruction.strip()}. Keep everything else in the image the same."
    progress.report("Sending your change to GPT Image 2.5 (image edit)...")
    data = _wavespeed_job("edit", {"prompt": prompt[:4000], "aspect_ratio": aspect}, "edited", src=src)
    if not data:
        raise RuntimeError("GPT Image 2.5 couldn't make that change right now. Please try again in a minute.")
    if out.exists():
        out.with_name(out.stem + ".prev" + out.suffix).write_bytes(out.read_bytes())
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(data))
    img.convert("RGB").save(out) if out.suffix.lower() in (".jpg", ".jpeg") else img.save(out)
    return out


def _story_dir(item: PrioritizedNewsItem, total: int = 4) -> Path:
    """4-image carousels keep the original folder (existing photos stay valid); other sizes get their own."""
    base = VISUALS_CACHE_DIR / _story_key(item)
    return base if total == 4 else base / f"n{total}"


def story_photo(item: PrioritizedNewsItem, index: int, total: int = 4) -> Path:
    """Where the clean (no text) photo for post `index` (1-total) of a story is stored."""
    return _story_dir(item, total) / f"photo_{index}.jpg"


def _render(prompt: str, seed: int, out_path: Path, llm: LLMClient,
            label: str = "photo", on_done=None, aspect: str = "4:5") -> Optional[Path]:
    full_prompt = prompt + IMAGE_SUFFIX
    data = _render_with_wavespeed(full_prompt, label, aspect)  # GPT Image 2.5 only, no fallbacks
    if on_done:
        on_done(bool(data))
    if not data:
        return None
    out_path.write_bytes(data)
    return out_path


async def prepare_post_assets(item: PrioritizedNewsItem, llm: Optional[LLMClient] = None,
                              count: int = 4, total: int = 4) -> Tuple[List[Dict[str, str]], List[Optional[Path]]]:
    """Returns (`total` post plans with headline/text, photo paths or None where rendering failed).

    Only the first `count` photos are rendered (the single image needs just the cover of the 4-image plan).
    """
    key = _story_key(item)
    story_dir = _story_dir(item, total)
    story_dir.mkdir(parents=True, exist_ok=True)
    paths = [story_dir / f"photo_{i}.jpg" for i in range(1, total + 1)]

    llm = llm or LLMClient(settings)
    plan_file = story_dir / "plan.json"
    if plan_file.exists():
        progress.report("Reusing the photo plan already made for this story...")
        posts = json.loads(plan_file.read_text(encoding="utf-8"))
    else:
        progress.report("Reading the story and planning relevant photos and headlines...")
        posts = await _plan_posts(item, llm, total)
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
