"""Alfred Studio: 9:16 split-screen videos and images, from a chat brief, a news story or his own script.

Video pipeline (nothing paid runs until he taps "Render"):
  1. Gemini writes the script as time-coded clips (what he says, what the top half shows, how it moves).
  2. Each clip gets a 16:9 image from the image models (Nano Banana, Flux fallback).
  3. In parallel:  HeyGen (Avatar III) renders ONE continuous take of him saying the whole script,
                   and each image becomes a short content clip (Gemini Omni Flash, or a free FFmpeg zoom).
  4. Clip lengths are synced to his real voice, then FFmpeg stacks: content on the top 65%, his clone on the
     bottom 35%, subtitles just above the clone, cross-faded in the middle with no hard edge, deep-black fades at the top and bottom.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config.settings import settings
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STUDIO_DIR = BASE_DIR / "artifacts" / "studio"
JOBS_FILE = STUDIO_DIR / "jobs.json"
HEYGEN = "https://api.heygen.com"
WORDS_PER_SECOND = 2.75  # his real pace with the 10% faster voice (27 words = ~9.6 s)
VIDEO_LENGTHS = [10, 20, 30, 45, 60]  # seconds offered in the app
DEFAULT_SECONDS = 30

_llm = LLMClient(settings)

PLAN_PROMPT = """You are the creative director of Master Ashok's (@ashok_a.i) short-form AI videos.
Plan a vertical 9:16 split-screen video ({seconds} seconds): the TOP half shows content visuals, the BOTTOM half
shows Ashok's AI avatar explaining.

{source}

Return ONLY JSON:
{{"title": "<short title>",
  "hook": "<the opening line, max 12 words, scroll-stopping>",
  "scenes": [
    {{"spoken": "<exactly what Ashok says in this clip>",
      "on_screen": "<what the top half shows, one sentence>",
      "visual": "<photorealistic 16:9 image prompt for the top half, 25-45 words, the concrete subject, no text>",
      "motion": "<how that image comes alive as a short video: camera move + what moves, 10-25 words>"}}
  ]}}

Rules:
- LENGTH IS STRICT: the whole script is {words_lo}-{words_hi} spoken words in total (= {seconds} seconds at his pace).
- Exactly {scenes} scene(s), about {per_scene} spoken words each. Scene 1 speaks the hook; the last scene lands the
  key takeaway. Plain, punchy spoken English.
- NEVER add a call to action like "Follow for more updates" (his cloned voice says it badly).
- Visuals must show the actual subject of that clip (product, device, place, result), never generic people at laptops."""

SCRIPT_SOURCE = """Use HIS SCRIPT below. Keep his words EXACTLY, in order: do not add, remove or reword anything.
Only split it into scenes and plan the visuals. The hook is his first sentence.
His script:
\"\"\"{script}\"\"\""""

BRIEF_SOURCE = """Write the script yourself from this brief:
\"\"\"{brief}\"\"\""""

IMAGE_PROMPT = """Turn this image request into a prompt for an image model and pick the aspect ratio.
Request: \"\"\"{request}\"\"\"
Keep his subject and every detail he asked for; add composition, lighting and style only where he didn't specify.
Return ONLY JSON: {{"prompt": "<40-80 word image prompt, no text in the image unless he asked for text>",
"aspect": "1:1" | "4:5" | "9:16" | "16:9"}}"""


# ----------------------------------------------------------------------------- jobs store
def _load_jobs() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8")) if JOBS_FILE.exists() else {}
    except Exception:
        return {}


def _save_job(job: Dict[str, Any]) -> None:
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    jobs = _load_jobs()
    job["updated"] = time.time()
    jobs[job["id"]] = job
    JOBS_FILE.write_text(json.dumps(jobs, indent=1, ensure_ascii=False), encoding="utf-8")


ENV_FILE = BASE_DIR / ".env"
RESOLVED_FILE = STUDIO_DIR / "heygen.json"


def heygen_env() -> Dict[str, str]:
    """HeyGen settings read fresh from .env on every use, so adding the key needs no server restart."""
    values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(ENV_FILE).items()}
    except Exception as e:
        logger.warning(f"Could not read .env: {e}")
    return {
        "key": values.get("HEYGEN_API_KEY") or (settings.HEYGEN_API_KEY or "").strip(),
        "avatar_id": values.get("HEYGEN_AVATAR_ID", (settings.HEYGEN_AVATAR_ID or "").strip()),
        "voice_id": values.get("HEYGEN_VOICE_ID") or (settings.HEYGEN_VOICE_ID or "").strip(),
        # avatar_iii (cheapest, ~$1/min) | avatar_iv | avatar_v (best, most expensive)
        "engine": (values.get("HEYGEN_ENGINE") or "avatar_iii").lower(),
    }


def video_env() -> Dict[str, str]:
    """Top-half video engine settings, read fresh from .env.
    TOP_VIDEO_ENGINE: wan (Alibaba Wan 2.6 Flash) | omni_flash (Gemini Omni Flash) | kenburns (free zoom/pan)."""
    values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(ENV_FILE).items()}
    except Exception:
        pass
    return {"engine": (values.get("TOP_VIDEO_ENGINE") or "wan").lower(),
            "resolution": values.get("TOP_VIDEO_RESOLUTION") or "720p"}


# API prices (USD) for the estimate before rendering and the receipt after (see src/costs.py).
from src import costs
from src.costs import HEYGEN_PER_MIN, OMNI_PER_SEC, WAN_PER_SEC
ENGINE_NAMES = {"avatar_iii": "Avatar III", "avatar_iv": "Avatar IV", "avatar_v": "Avatar V"}


def estimate_cost(plan: Dict[str, Any]) -> Dict[str, Any]:
    secs = max(1, plan.get("duration_sec", 40))
    hg_engine, top = heygen_env()["engine"], video_env()
    heygen = round(HEYGEN_PER_MIN.get(hg_engine, 1.0) * secs / 60, 2)
    rate = {"omni_flash": OMNI_PER_SEC.get(top["resolution"], 0.10),
            "wan": WAN_PER_SEC.get(top["resolution"], 0.025)}.get(top["engine"], 0.0)
    content = round(rate * secs, 2)
    return {"heygen_usd": heygen, "content_usd": content, "total_usd": round(heygen + content, 2),
            "heygen_engine": hg_engine, "top_engine": top["engine"], "top_resolution": top["resolution"],
            "seconds": secs}


def heygen_ready() -> Optional[str]:
    """None if HeyGen can be used, else the reason it can't."""
    if not heygen_env()["key"]:
        return "HEYGEN_API_KEY is empty in .env (HeyGen → Settings → API)"
    return None


def _headers() -> Dict[str, str]:
    return {"x-api-key": heygen_env()["key"]}


async def _list_all(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    items, token = [], None
    for _ in range(10):
        p = dict(params, limit=50 if "looks" in path else 100, **({"token": token} if token else {}))
        r = await client.get(f"{HEYGEN}{path}", headers=_headers(), params=p)
        r.raise_for_status()
        body = r.json()
        items += body.get("data") or []
        token = body.get("next_token")
        if not body.get("has_more") or not token:
            break
    return items


async def resolve_heygen(force: bool = False) -> Dict[str, Any]:
    """Checks the key and picks the avatar + voice to use, cached until the .env values change.

    Avatar: HEYGEN_AVATAR_ID if it is one of his looks, else his own digital twin (then any of his looks).
    Voice: HEYGEN_VOICE_ID if set, else the avatar's default voice, else his first cloned voice.
    """
    env = heygen_env()
    if not env["key"]:
        raise RuntimeError("HEYGEN_API_KEY is empty in .env (HeyGen → Settings → API)")
    fingerprint = hashlib.sha1(f"{env['key']}|{env['avatar_id']}|{env['voice_id']}|{env['engine']}".encode()).hexdigest()
    try:
        cached = json.loads(RESOLVED_FILE.read_text(encoding="utf-8"))
        if not force and cached.get("fingerprint") == fingerprint:
            return cached
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=60) as client:
        me = await client.get(f"{HEYGEN}/v3/users/me", headers=_headers())
        if me.status_code == 401:
            raise RuntimeError("HeyGen rejected HEYGEN_API_KEY (unauthorized). Copy it again from HeyGen → Settings → API.")
        me.raise_for_status()
        user = me.json().get("data") or {}

        looks = await _list_all(client, "/v3/avatars/looks", {"ownership": "private"})
        ready = [lk for lk in looks if lk.get("status") in (None, "completed", "ready", "active")]

        def supports(lk: Dict[str, Any]) -> bool:
            engines = lk.get("supported_api_engines")
            return not engines or env["engine"] in [str(e).lower() for e in engines]

        ready = sorted(ready, key=lambda lk: not supports(lk))  # looks that support the engine first
        look = next((lk for lk in looks if lk.get("id") == env["avatar_id"]), None)
        how_avatar = "from HEYGEN_AVATAR_ID"
        if not look:
            look = (next((lk for lk in ready if lk.get("avatar_type") == "digital_twin"), None)
                    or (ready[0] if ready else None))
            how_avatar = "auto-picked (your own avatar)" if look else ""
        if not look and env["avatar_id"]:
            look = {"id": env["avatar_id"], "name": "(set in .env, not found among your avatars)"}
            how_avatar = "from HEYGEN_AVATAR_ID (unverified)"
        if not look:
            raise RuntimeError("No avatar found in your HeyGen account. Create your digital twin in HeyGen first.")

        engines = [str(e).lower() for e in (look.get("supported_api_engines") or [])]
        if engines and env["engine"] not in engines:
            raise RuntimeError(f"Your avatar '{look.get('name', look['id'])}' doesn't support {env['engine']} "
                               f"(it supports: {', '.join(engines)}). Set HEYGEN_ENGINE in .env to one of those.")

        voice_id, how_voice = env["voice_id"], "from HEYGEN_VOICE_ID"
        voices = await _list_all(client, "/v3/voices", {"type": "private"})
        if not voice_id:
            voice_id, how_voice = look.get("default_voice_id"), "avatar's default voice"
        if not voice_id and voices:
            voice_id, how_voice = voices[0]["voice_id"], "auto-picked (your cloned voice)"
        if not voice_id:
            raise RuntimeError("No voice found: set HEYGEN_VOICE_ID in .env or add a voice to your avatar in HeyGen.")
        voice_name = next((v["name"] for v in voices if v["voice_id"] == voice_id), "")

    wallet, sub, usage = user.get("wallet") or {}, user.get("subscription") or {}, user.get("usage_based") or {}
    if wallet.get("remaining_balance") is not None:
        credits = f"{wallet['remaining_balance']} {wallet.get('currency', '')}".strip()
    elif sub:
        credits = ((sub.get("credits") or {}).get("premium_credits") or {}).get("remaining")
    else:
        credits = usage.get("remaining_credits")
    resolved = {
        "fingerprint": fingerprint,
        "account": user.get("email") or user.get("username") or "",
        "plan": (sub.get("plan") if sub else user.get("billing_type")) or "",
        "credits": credits,
        "avatar_id": look["id"], "avatar_name": look.get("name", ""), "avatar_type": look.get("avatar_type", ""),
        "avatar_source": how_avatar, "engine": env["engine"],
        "avatar_engines": look.get("supported_api_engines") or [],
        "voice_id": voice_id, "voice_name": voice_name, "voice_source": how_voice,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    RESOLVED_FILE.write_text(json.dumps(resolved, indent=1), encoding="utf-8")
    logger.info(f"HeyGen ready: avatar {resolved['avatar_name']} ({how_avatar}), voice {voice_id} ({how_voice})")
    return resolved


# ----------------------------------------------------------------------------- planning
def _fmt(sec: float) -> str:
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


CTA_RE = re.compile(r"[^.!?]*\b(follow|subscribe)\b[^.!?]*\bfor more\b[^.!?]*[.!?]?", re.I)


def _drop_cta(scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Removes "Follow for more (AI) updates"-style lines: his cloned voice says them badly.
    A scene left with nothing to say is dropped."""
    kept = []
    for s in scenes:
        spoken = " ".join(CTA_RE.sub(" ", str(s.get("spoken", ""))).split())
        if spoken:
            kept.append({**s, "spoken": spoken})
    return kept


def _split_script(script: str, max_words: int = 20) -> List[str]:
    """His script -> clips by sentence (short sentences merged, long ones split), words untouched."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(script.split())) if s]
    clips: List[str] = []
    for sent in sentences:
        words = sent.split()
        while len(words) > max_words:  # split very long sentences at a comma if possible
            cut = next((i + 1 for i in range(max_words - 1, 5, -1) if words[i].endswith(",")), max_words)
            clips.append(" ".join(words[:cut]))
            words = words[cut:]
        part = " ".join(words)
        if clips and len(part.split()) < 6 and len(clips[-1].split()) + len(part.split()) <= max_words:
            clips[-1] += " " + part
        elif part:
            clips.append(part)
    return clips


VISUALS_PROMPT = """Plan the top-half visuals for a vertical 9:16 split-screen video by Master Ashok (@ashok_a.i):
content on top, his avatar explaining on the bottom. The spoken clips are FIXED; do not change them.

Clips:
{clips}

Return ONLY JSON: {{"title": "<short title>", "scenes": [{{
"on_screen": "<what the top half shows, one sentence>",
"visual": "<photorealistic 16:9 image prompt, 25-45 words, the concrete subject, no text>",
"motion": "<how that image comes alive as a short video: camera move + what moves, 10-25 words>"}}]}}
Exactly {n} scenes, in order. Visuals must show the actual subject of each clip, never generic people at laptops."""


def _length_rules(seconds: int) -> Dict[str, int]:
    """Target words and scene count for a video of `seconds` (one scene per ~6 s, 1-8 scenes)."""
    words = round(seconds * WORDS_PER_SECOND)
    scenes = max(1, min(8, round(seconds / 6)))
    return {"seconds": seconds, "words_lo": round(words * 0.9), "words_hi": round(words * 1.05),
            "scenes": scenes, "per_scene": round(words / scenes)}


async def plan_video(brief: str, custom_script: bool = False, seconds: Optional[int] = None) -> Dict[str, Any]:
    if custom_script:
        # His words are split by code and never sent back through the model, so they can't be changed.
        clips = _split_script(brief)
        if not clips:
            raise ValueError("The script is empty")
        data = await _llm.generate_json(VISUALS_PROMPT.format(
            clips="\n".join(f"{i}. {c}" for i, c in enumerate(clips, 1)), n=len(clips)))
        planned = [s for s in (data.get("scenes") or []) if isinstance(s, dict)]
        scenes = []
        for i, clip in enumerate(clips):
            s = planned[i] if i < len(planned) else {}
            scenes.append({"spoken": clip, "on_screen": s.get("on_screen", ""),
                           "visual": s.get("visual") or f"Photorealistic wide image representing: {clip}",
                           "motion": s.get("motion") or "Slow cinematic push-in with subtle natural movement"})
        data = {"title": data.get("title"), "hook": clips[0]}
    else:
        data = await _llm.generate_json(PLAN_PROMPT.format(source=BRIEF_SOURCE.format(brief=brief),
                                                           **_length_rules(int(seconds or DEFAULT_SECONDS))))
        scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and s.get("spoken")]
    scenes = _drop_cta(scenes)
    if not scenes:
        raise ValueError("The planner returned no scenes")
    t = 0.0
    for s in scenes:  # estimated timings; the final ones are synced to his real voice after HeyGen renders
        s.setdefault("motion", "Slow cinematic push-in with subtle natural movement")
        dur = max(2.0, len(str(s["spoken"]).split()) / WORDS_PER_SECOND)
        s["start"], s["end"] = _fmt(t), _fmt(t + dur)
        t += dur
    return {"title": data.get("title") or "Alfred video", "hook": data.get("hook") or scenes[0]["spoken"],
            "scenes": scenes, "duration_sec": round(t), "target_sec": seconds}


def plan_markdown(plan: Dict[str, Any]) -> str:
    lines = [f"**{plan['title']}** (9:16 split screen, about {plan['duration_sec']}s)", "",
             f"**Hook:** \"{plan['hook']}\"", "",
             "Top half: content video · Bottom half: you explaining (HeyGen)", ""]
    for i, s in enumerate(plan["scenes"], 1):
        lines.append(f"**Clip {i} ({s['start']}-{s['end']})**")
        lines.append(f"Says: \"{s['spoken']}\"")
        lines.append(f"Top shows: {s.get('on_screen', '')}")
        lines.append("")
    return "\n".join(lines).strip()


# ----------------------------------------------------------------------------- images
def _render_image(prompt: str, aspect: str, out: Path, label: str) -> Optional[Path]:
    from src.video.slide_visuals import _render
    return _render(prompt, int(time.time()) % 100000, out, _llm, label=label, aspect=aspect)


async def make_image(request: str, aspect: Optional[str] = None, exact_prompt: bool = False) -> Dict[str, Any]:
    """Image from a chat request (enhanced by Gemini) or his own prompt (used as written)."""
    if exact_prompt:
        prompt, aspect = request, aspect or "4:5"
    else:
        data = await _llm.generate_json(IMAGE_PROMPT.format(request=request))
        prompt = str(data.get("prompt") or request)
        aspect = aspect or (data.get("aspect") if data.get("aspect") in ("1:1", "4:5", "9:16", "16:9") else "4:5")
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    name = f"img_{uuid.uuid4().hex[:10]}.png"
    ledger = costs.start()
    path = await asyncio.to_thread(_render_image, prompt, aspect, STUDIO_DIR / name, "your image")
    if not path:
        raise RuntimeError("All image models failed (quota or network). Please try again in a minute.")
    return {"file": name, "url": f"/api/v1/studio/files/{name}", "prompt": prompt, "aspect": aspect,
            "usage": await costs.summary(ledger)}


# ----------------------------------------------------------------------------- avatar picker
AVATARS_FILE = STUDIO_DIR / "avatars.json"
AVATAR_THUMB = 160  # px: the app shows small tiles


async def list_avatars(refresh: bool = False) -> List[Dict[str, Any]]:
    """His HeyGen looks for the picker: id, name, default voice and a small local thumbnail (HeyGen's preview
    links expire, so each preview is saved once as avatar_<id>.jpg). Cached for 12 h."""
    from PIL import Image
    import io
    items: List[Dict[str, Any]] = []
    try:
        cached = json.loads(AVATARS_FILE.read_text(encoding="utf-8"))
        if not refresh and time.time() - cached.get("time", 0) < 12 * 3600:
            items = cached["items"]
    except Exception:
        pass
    if not items:
        engine = heygen_env()["engine"]
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            looks = await _list_all(client, "/v3/avatars/looks", {"ownership": "private"})
            for lk in looks:
                engines = [str(e).lower() for e in (lk.get("supported_api_engines") or [])]
                if lk.get("status") not in (None, "completed", "ready", "active") or (engines and engine not in engines):
                    continue
                thumb = STUDIO_DIR / f"avatar_{lk['id']}.jpg"
                if not thumb.exists() and lk.get("preview_image_url"):
                    try:
                        img = Image.open(io.BytesIO((await client.get(lk["preview_image_url"])).content)).convert("RGB")
                        img.thumbnail((AVATAR_THUMB * 2, AVATAR_THUMB * 2))
                        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
                        img.save(thumb, quality=85)
                    except Exception as e:
                        logger.warning(f"Avatar thumbnail failed for {lk['id']}: {e}")
                items.append({"id": lk["id"], "name": lk.get("name") or "Avatar", "type": lk.get("avatar_type"),
                              "voice_id": lk.get("default_voice_id"),
                              "thumb": f"/api/v1/studio/files/{thumb.name}" if thumb.exists() else None})
        AVATARS_FILE.write_text(json.dumps({"time": time.time(), "items": items}, indent=1), encoding="utf-8")
    current = heygen_env()["avatar_id"]
    return [{**a, "current": a["id"] == current} for a in items]


# ----------------------------------------------------------------------------- captions
CAPTION_PROMPT = """Write the post caption for this short vertical video by Master Ashok (@ashok_a.i), for Instagram,
LinkedIn and YouTube Shorts.
Title: {title}
What he says: \"\"\"{script}\"\"\"
Rules: 2-3 short lines in plain English (a hook, then the key point), then 5-8 relevant hashtags on the last line.
At most 2 emojis, no "follow for more", no facts beyond what he says.
Return ONLY JSON: {{"caption": "<the caption>"}}"""


async def make_caption(plan: Dict[str, Any]) -> str:
    script = " ".join(s["spoken"] for s in plan["scenes"])
    try:
        data = await _llm.generate_json(CAPTION_PROMPT.format(title=plan.get("title", ""), script=script))
        caption = str(data.get("caption") or "").strip()
        if caption:
            return caption
    except Exception as e:
        logger.warning(f"Caption generation failed: {e}")
    return f"{plan.get('title', '')}\n\n{script}\n\n#AI #AINews #Tech"


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _load_jobs().get(job_id)


def update_job(job: Dict[str, Any], **fields: Any) -> None:
    job.update(fields)
    _save_job(job)


# ----------------------------------------------------------------------------- progress
# Overall % = top half (images + clips) 33% + HeyGen take 50% + polish/stitch 15%, plus 2% for starting.
# The app shows the % and a playful word for the current stage (imagining, animating, recording, ...).
def _progress(job: Dict[str, Any], stage: Optional[str] = None, **parts: float) -> None:
    job.update({f"_{k}": v for k, v in parts.items()})
    pct = 2 + 33 * job.get("_top", 0) + 50 * job.get("_hg", 0) + 15 * job.get("_post", 0)
    job["progress"] = max(job.get("progress", 0), min(99, int(pct)))
    if stage:
        job["stage"] = stage
    _save_job(job)


# ----------------------------------------------------------------------------- HeyGen
async def _heygen_take(client: httpx.AsyncClient, hg: Dict[str, Any], script: str, job: Dict[str, Any]) -> Path:
    """ONE continuous avatar take of the whole script (landscape, filled), downloaded when ready."""
    from src.video.split_video import AVATAR_SPEED
    body = {"type": "avatar", "avatar_id": hg["avatar_id"], "voice_id": hg["voice_id"], "script": script,
            # HeyGen speaks faster itself and animates the lips to it at full frame rate (no dropped frames)
            "voice_settings": {"speed": AVATAR_SPEED},
            "engine": {"type": hg["engine"]}, "aspect_ratio": "16:9", "fit": "cover", "resolution": "1080p",
            "title": f"Alfred: {job['plan']['title'][:60]}"}
    r = await client.post(f"{HEYGEN}/v3/videos", headers=_headers(), json=body)
    if r.status_code != 200:
        raise RuntimeError(f"HeyGen error {r.status_code}: {r.text[:300]}")
    video_id = r.json()["data"]["video_id"]
    job["video_id"] = video_id
    _save_job(job)
    import math
    expected = 120 + 20 * len(script.split()) / WORDS_PER_SECOND  # HeyGen gives no %: ~2-8 min for 10 s
    for i in range(360):  # up to 60 minutes (a 10 s test took ~8.5 min; 50 s can take much longer)
        await asyncio.sleep(10)
        d = (await client.get(f"{HEYGEN}/v3/videos/{video_id}", headers=_headers())).json().get("data") or {}
        job["heygen_status"] = f"{d.get('status', 'processing')}, {(i + 1) * 10 // 60} min"
        if job.get("top_engines"):  # top half done: show HeyGen's live progress in the chat
            job["message"] = f"Top half ready. HeyGen is rendering your clone ({job['heygen_status']})..."
        # time-based: moves quickly at first, then slows, never stalls; heartbeat for the job too
        _progress(job, "recording" if job.get("top_engines") else None,
                  hg=min(0.95, 1 - math.exp(-(i + 1) * 10 / expected)))
        if d.get("status") == "completed" and d.get("video_url"):
            out = STUDIO_DIR / f"{job['id']}_avatar.mp4"
            out.write_bytes((await client.get(d["video_url"])).content)
            from src.video.split_video import media_duration
            secs = float(d.get("duration") or media_duration(out))
            _progress(job, hg=1.0)
            costs.record("HeyGen", f"Avatar video · {ENGINE_NAMES.get(hg['engine'], hg['engine'])}, {secs:.1f}s",
                         HEYGEN_PER_MIN.get(hg["engine"], 1.0) * secs / 60)
            return out
        if d.get("status") == "failed":
            raise RuntimeError(f"HeyGen failed: {d.get('failure_message') or d.get('failure_code')}")
    raise RuntimeError("HeyGen is still rendering after 60 minutes; check the HeyGen dashboard")


async def _content_sources(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per scene: a 16:9 image, then a content clip from the top engine (or just the image for the free zoom)."""
    from src.video import split_video
    plan, top = job["plan"], video_env()
    sources: List[Dict[str, Any]] = []
    n = len(plan["scenes"])
    for i, sc in enumerate(plan["scenes"], 1):
        job["message"] = f"Creating image {i} of {len(plan['scenes'])} for the top half..."
        _progress(job, "imagining", top=(i - 1) / n)
        img = await asyncio.to_thread(_render_image, sc["visual"], "16:9", STUDIO_DIR / f"{job['id']}_scene{i}.png",
                                      f"clip {i} image")
        if not img:
            raise RuntimeError(f"All image models failed for clip {i}")
        src: Dict[str, Any] = {"image": str(img), "engine": "kenburns"}
        _progress(job, "animating" if top["engine"] in ("wan", "omni_flash") else None,
                  top=(i - 0.5) / n if top["engine"] in ("wan", "omni_flash") else i / n)
        if top["engine"] == "wan":
            job["message"] = f"Animating clip {i} of {len(plan['scenes'])} with Wan 2.6 Flash..."
            _save_job(job)
            seconds = len(sc["spoken"].split()) / WORDS_PER_SECOND + 1  # a little longer than the line; trimmed later
            try:
                clip = await asyncio.to_thread(split_video.wan_clip, img, f"{sc.get('motion', '')}. {sc['visual']}",
                                               seconds, STUDIO_DIR / f"{job['id']}_clip{i}.mp4", top["resolution"])
                src.update(video=str(clip), engine="wan")
            except Exception as e:  # no key / free seconds used up / error: keep going with the free zoom
                logger.warning(f"Wan unavailable for clip {i}, using free zoom instead: {e}")
                src["note"] = str(e)[:200]
        elif top["engine"] == "omni_flash":
            job["message"] = f"Animating clip {i} of {len(plan['scenes'])} with Gemini Omni Flash..."
            _save_job(job)
            try:
                clip = await asyncio.to_thread(split_video.omni_clip, img, f"{sc.get('motion', '')}. {sc['visual']}",
                                               STUDIO_DIR / f"{job['id']}_clip{i}.mp4", top["resolution"])
                src.update(video=str(clip), engine="omni_flash")
                secs = split_video.media_duration(clip)
                costs.record("Google Gemini", f"Video clip · Omni Flash {top['resolution']}, {secs:.0f}s",
                             OMNI_PER_SEC.get(top["resolution"], 0.10) * secs)
            except Exception as e:  # no billing / quota / old SDK: keep going with the free zoom
                logger.warning(f"Omni Flash unavailable for clip {i}, using free zoom instead: {e}")
                src["note"] = str(e)[:200]
        sources.append(src)
        _progress(job, top=i / n)
    return sources


_build_tasks: Dict[str, "asyncio.Task"] = {}


async def _build(job_id: str) -> None:
    from src.video import split_video
    job = _load_jobs()[job_id]
    ledger = costs.start()
    avatar_task = None
    try:
        hg = await resolve_heygen()  # fail fast (bad key / no avatar) before spending anything
        if job.get("engine"):  # one-off engine for this job (e.g. an Avatar IV test); .env stays as it is
            hg = {**hg, "engine": job["engine"]}
        if job.get("avatar_id") and job["avatar_id"] != hg["avatar_id"]:  # picked in the app for this video
            look = next((a for a in await list_avatars() if a["id"] == job["avatar_id"]), {})
            hg = {**hg, "avatar_id": job["avatar_id"],
                  "voice_id": heygen_env()["voice_id"] or look.get("voice_id") or hg["voice_id"]}
        plan = job["plan"]
        plan["scenes"] = _drop_cta(plan["scenes"]) or plan["scenes"]
        script = " ".join(s["spoken"] for s in plan["scenes"])
        job.update(status="rendering", message="HeyGen is recording your clone; creating the top-half visuals...")
        _progress(job, "imagining")
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            avatar_task = asyncio.create_task(_heygen_take(client, hg, script, job))
            sources = await _content_sources(job)
            job.update(message="Top half ready. Waiting for HeyGen to finish your clone (can take 10-40 minutes)...",
                       top_engines=[s["engine"] for s in sources])
            _save_job(job)
            raw_avatar = await avatar_task

        avatar = raw_avatar  # HeyGen's take as it is (already 10% faster via voice_settings)
        if split_video.STEADY_HEAD:
            job["message"] = "Polishing your clone: steadier head..."
            _progress(job, "polishing", top=1.0, hg=1.0)
            avatar = await asyncio.to_thread(split_video.polish_avatar, raw_avatar,
                                             STUDIO_DIR / f"{job_id}_avatar_fast.mp4", speed=1.0)

        # Sync clip lengths to his real voice: split the take's duration by each clip's share of the words.
        total = split_video.media_duration(avatar)
        words = [max(1, len(s["spoken"].split())) for s in plan["scenes"]]
        durations = [total * w / sum(words) for w in words]

        job["message"] = "Putting it together: content on top, you on the bottom..."
        _progress(job, "stitching", post=0.4)
        clips: List[Path] = []
        for i, (src, d) in enumerate(zip(sources, durations), 1):
            if src.get("video"):
                clips.append(Path(src["video"]))
            else:
                clips.append(await asyncio.to_thread(split_video.kenburns_clip, Path(src["image"]), d + 0.5,
                                                     STUDIO_DIR / f"{job_id}_kb{i}.mp4", i))
        name = f"{job_id}.mp4"
        _progress(job, "finishing", post=0.6)
        await asyncio.to_thread(split_video.compose, clips, durations, avatar, STUDIO_DIR / name,
                                STUDIO_DIR / f"{job_id}_work", script)
        job["caption"] = await make_caption(plan)  # editable in the app before posting
        job.update(status="done", message="Your video is ready.", file=name, url=f"/api/v1/studio/files/{name}",
                   duration=round(total, 1), usage=await costs.summary(ledger), progress=100, stage="done")
        _save_job(job)
    except asyncio.CancelledError:  # he pressed Cancel during the render
        if avatar_task and not avatar_task.done():
            avatar_task.cancel()
        job.update(status="cancelled", message="Render cancelled. Nothing more will be created; "
                   "anything already started was charged (see below).", usage=await costs.summary(ledger))
        _save_job(job)
    except Exception as e:
        logger.error(f"Studio job {job_id} failed: {e}")
        job.update(status="failed", message=str(e)[:300], usage=await costs.summary(ledger))  # what was spent anyway
        _save_job(job)
    finally:
        _build_tasks.pop(job_id, None)


async def start_video_job(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Creates the job WAITING FOR APPROVAL: nothing paid runs until he taps Render (approve_job)."""
    job = {"id": uuid.uuid4().hex[:12], "kind": "video", "plan": plan, "status": "awaiting_approval",
           "estimate": estimate_cost(plan), "message": "Review the plan, then tap Render.", "created": time.time()}
    reason = heygen_ready()
    if reason:
        job.update(status="failed", message=f"HeyGen isn't set up: {reason}.")
    _save_job(job)
    return job


async def approve_job(job_id: str, avatar_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    job = _load_jobs().get(job_id)
    if not job:
        return None
    if job["status"] == "awaiting_approval":
        if avatar_id:
            job["avatar_id"] = avatar_id
        job.update(status="queued", message="Starting...", approved=time.time(), progress=0, stage="starting")
        _save_job(job)
        _build_tasks[job_id] = asyncio.create_task(_build(job_id))
    return {k: v for k, v in job.items() if k != "plan" and not k.startswith("_")}


async def cancel_job(job_id: str) -> Optional[Dict[str, Any]]:
    job = _load_jobs().get(job_id)
    if job and job["status"] == "awaiting_approval":
        job.update(status="cancelled", message="Cancelled. Nothing was rendered or charged.")
        _save_job(job)
    elif job and job["status"] in ("queued", "rendering") and job_id in _build_tasks:
        _build_tasks[job_id].cancel()  # _build marks it cancelled and saves what was spent
        await asyncio.sleep(0.3)
        job = _load_jobs().get(job_id, job)
    return {k: v for k, v in job.items() if k != "plan" and not k.startswith("_")} if job else None


async def job_status(job_id: str) -> Optional[Dict[str, Any]]:
    job = _load_jobs().get(job_id)
    if not job:
        return None
    busy = job["status"] in ("queued", "rendering")
    if busy and time.time() - job.get("updated", 0) > 900:  # no heartbeat for 15 min = the server restarted
        job.update(status="failed", message="The job was interrupted (server restarted). Please ask again.")
        _save_job(job)
    return {k: v for k, v in job.items() if k != "plan" and not k.startswith("_")}


async def _cli_check() -> None:
    try:
        info = await resolve_heygen(force=True)
        print("HeyGen is ready:")
        for k in ("account", "plan", "credits", "engine", "avatar_name", "avatar_id", "avatar_source",
                  "avatar_engines", "voice_name", "voice_id", "voice_source"):
            print(f"  {k:14} {info.get(k)}")
    except Exception as e:
        print(f"HeyGen is NOT ready: {e}")
    from src.video.slide_visuals import _openai_env
    oi = _openai_env()
    print(f"Images: 1. {oi['wavespeed_model']} ({oi['wavespeed_resolution']}, WaveSpeed) · key: "
          f"{'set' if oi['wavespeed_key'] else 'MISSING (WAVESPEED_API_KEY)'}")
    print("        2. Nano Banana (free tier) · 3. Flux (free)")
    from src.video.split_video import _dashscope_env
    top, wan = video_env(), _dashscope_env()
    print(f"Top half: {top['engine']} at {top['resolution']}")
    if top["engine"] == "wan":
        if wan["provider"] == "wavespeed":
            print(f"  Wan 2.6 Flash via WaveSpeed AI · key: {'set' if wan['wavespeed_key'] else 'MISSING (WAVESPEED_API_KEY)'}")
        else:
            print(f"  Wan via Alibaba Cloud · key: {'set' if wan['key'] else 'MISSING (DASHSCOPE_API_KEY)'} · {wan['base']}")


def studio_file(name: str) -> Optional[Path]:
    if not re.fullmatch(r"[A-Za-z0-9_]+\.(mp4|png|jpg)", name):
        return None
    path = STUDIO_DIR / name
    return path if path.exists() else None


TEST_SCRIPT = ("Hi, I'm Ashok's AI avatar. This is a quick test of HeyGen from Alfred, "
               "in nine by sixteen, ready for Instagram and YouTube.")


async def _cli_test(script: str = TEST_SCRIPT) -> None:
    """One ~10 second 9:16 avatar clip (no images), to check quality and lip-sync cheaply."""
    info = await resolve_heygen(force=True)
    print(f"Test: {info['engine']} · avatar {info['avatar_name'] or info['avatar_id']} · voice {info['voice_name'] or info['voice_id']}")
    body = {"type": "avatar", "avatar_id": info["avatar_id"], "voice_id": info["voice_id"], "script": script,
            "engine": {"type": info["engine"]}, "aspect_ratio": "9:16", "resolution": "1080p",
            "title": f"Alfred test ({info['engine']})"}
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        r = await client.post(f"{HEYGEN}/v3/videos", headers=_headers(), json=body)
        if r.status_code != 200:
            print(f"HeyGen refused the test ({r.status_code}): {r.text[:400]}")
            return
        vid = r.json()["data"]["video_id"]
        print(f"Submitted video {vid}; waiting for HeyGen (usually 1-5 minutes)...")
        for i in range(120):  # up to ~20 minutes
            await asyncio.sleep(10)
            d = (await client.get(f"{HEYGEN}/v3/videos/{vid}", headers=_headers())).json().get("data") or {}
            if d.get("status") == "completed" and d.get("video_url"):
                STUDIO_DIR.mkdir(parents=True, exist_ok=True)
                out = STUDIO_DIR / f"test_{info['engine']}.mp4"
                out.write_bytes((await client.get(d["video_url"])).content)
                print(f"Done in ~{(i + 1) * 10}s, {d.get('duration')}s long -> {out}")
                return
            if d.get("status") == "failed":
                print(f"HeyGen failed: {d.get('failure_message') or d.get('failure_code')}")
                return
            if i % 3 == 2:
                print(f"  still {d.get('status', 'processing')} ({(i + 1) * 10}s)...")
        print("Still rendering after 20 minutes; check the HeyGen dashboard.")


async def _cli_wan_test(image: Optional[str] = None) -> None:
    """ONE 5-second silent 720p Wan clip from an existing image (uses 5 of the 50 free seconds)."""
    from src.video import split_video
    src = Path(image) if image else next(iter(sorted(STUDIO_DIR.glob("img_*.png"))), None)
    if not src or not src.exists():
        print("No image found: pass one, e.g.  python -m src.studio wan-test artifacts/studio/img_xxx.png")
        return
    out = STUDIO_DIR / "test_wan.mp4"
    print(f"Wan test: {src.name} -> 5 s, 720P, silent. Waiting for Alibaba (usually 1-5 minutes)...")
    try:
        await asyncio.to_thread(split_video.wan_clip, src, "Slow cinematic camera push-in, subtle natural motion",
                                5, out, "720p")
        print(f"Done -> {out}")
    except Exception as e:
        print(f"Wan test failed: {e}")


if __name__ == "__main__":
    # python -m src.studio        -> checks the HeyGen setup (no cost)
    # python -m src.studio test   -> renders one ~10s 9:16 avatar test clip (uses a little HeyGen credit)
    # python -m src.studio wan-test [image]  -> one 5 s Wan 2.6 Flash clip (uses 5 free seconds)
    import sys
    cmd = sys.argv[1:2]
    if cmd == ["test"]:
        asyncio.run(_cli_test())
    elif cmd == ["wan-test"]:
        asyncio.run(_cli_wan_test(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        asyncio.run(_cli_check())
