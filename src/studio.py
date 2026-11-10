"""Alfred Studio: 9:16 split-screen videos and images, from a chat brief, a news story or his own script.

Video pipeline (nothing paid runs until he taps "Render"):
  1. Gemini writes the script as time-coded clips (what he says, what the top half shows, how it moves).
  2. Each clip gets a square 1:1 image (the top part is about square) from GPT Image 2.5 (WaveSpeed, no fallbacks).
  3. In parallel:  HeyGen (Avatar III) renders ONE continuous take of him saying the whole script,
                   and each image becomes a short content clip (Gemini Omni Flash, or a free FFmpeg zoom).
  4. Clip lengths are synced to his real voice, then FFmpeg stacks: content on the top 60%, his clone on the
     bottom 40%, subtitles just above the clone, cross-faded in the middle with no hard edge, clean top and bottom edges.
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
# Video scripts (what he says, what's shown) use Google's stronger Flash model: the words decide whether people
# keep watching. Everything else stays on the cheaper GEMINI_MODEL; if this one fails, the usual fallbacks run.
SCRIPT_MODEL = "gemini-3.8-flash"

_llm = LLMClient(settings)

PLAN_PROMPT = """You are the creative director of Master Ashok's (@ashok_a.i) short-form AI videos.
Plan a vertical 9:16 split-screen video ({seconds} seconds): the TOP half shows content visuals, the BOTTOM half
shows Ashok's AI avatar explaining.

{source}

Return ONLY JSON:
{{"title": "<short title>",
  "hook_options": ["<3 different hooks, each using a different hook style below>", "...", "..."],
  "hook": "<the strongest of the 3: the one a busy person scrolling would stop for, 100% true, max 12 words>",
  "scenes": [
    {{"spoken": "<exactly what Ashok says in this clip>",
      "on_screen": "<what the top half shows, one sentence>",
      "visual": "<photorealistic SQUARE 1:1 image prompt for the top half, subject centred, 25-45 words, the concrete subject, no text>",
      "motion": "<how that image comes alive as a short video: camera move + what moves, 10-25 words>"}}
  ]}}

{script_craft}

Rules:
- LENGTH IS STRICT: the whole script is {words_lo}-{words_hi} spoken words in total (= {seconds} seconds at his pace).
- Exactly {scenes} scene(s), about {per_scene} spoken words each. Scene 1 speaks the hook first, then the
  "why you should care" line; the last scene closes the loop the hook opened.
- NEVER add a call to action like "Follow for more updates" (his cloned voice says it badly).
- Visuals must show the actual subject of that clip (product, device, place, result), never generic people at laptops.
  Scene 1's visual must be the most striking image of the video (it is on screen during the hook).
- Famous people stop the scroll: a well-known person (e.g. Sam Altman for OpenAI, Elon Musk for xAI / Tesla,
  Sundar Pichai for Google, Jensen Huang for NVIDIA, Dario Amodei for Anthropic) may be the visual of at most
  2 scenes, ONLY when the story is directly about them or a BIG moment of the company they lead (flagship model
  launch, funding, big deal, keynote, lawsuit, leadership news) - never for smaller product features, tools,
  research or general industry stories. Realistic editorial press-photo style (keynote
  stage, launch-event portrait), never doing or saying anything the story doesn't report, never in a fake or
  embarrassing scene."""

SCRIPT_CRAFT = """HOW TO WRITE IT. Three goals, in this order: TRUE, SIMPLE, impossible to scroll past.

THE FIRST 5 SECONDS (about 12-14 spoken words) - this decides if people stay or swipe away
- Sentence 1 = THE HOOK (max 12 words). Lead with the single most surprising CONCRETE fact of the story:
  a number, a name, a result, a before/after. A viewer must get it instantly, on first hearing, with no context.
- Sentence 2 = WHY YOU SHOULD CARE (max 8 words): what it means for the viewer - their money, time, job, skills,
  or what they can now do. Still inside the first 5 seconds.
- Open a curiosity gap: make them need the rest, but keep the full answer for later in the script.
- Hook styles (use a different one for each of the 3 options):
  1. Shock fact: "Google's new AI just wrote 30% of its own code."
  2. Bold / contrarian claim: "Your coding assistant is about to become useless."
  3. Direct "you" stakes: "If you use ChatGPT for work, this changes what you pay."
  4. Tease the secret: "OpenAI quietly shipped something nobody is talking about."
  5. Before -> after: "This took a designer 3 days. Now it takes 30 seconds."
  These show the style only - write your own from the story's real facts, never copy them.
- NEVER open with: a greeting ("Hey guys"), "In today's video", "So,", "Did you know", "Imagine",
  "Have you ever", "What if I told you", "Let's talk about", "AI is changing the world", "Big news",
  or the company name followed by a dry announcement.

KEEP THEM WATCHING (the rest)
- Talk to ONE person, like a smart friend explaining it over chai: "you", "your", contractions.
- Every sentence adds one new concrete fact or moves the story forward. If a line can be cut without losing
  anything, cut it. Never repeat a point.
- Show, don't claim: a concrete example ("type 'make me a budget tracker' and it builds one") beats an adjective
  ("powerful", "amazing", "incredible").
- About halfway, re-hook once with a short surprising turn in your OWN words (a striking number, a catch, a
  "but"). Don't use stock lines like "But here's the wild part".
- Build to the payoff: the most useful or surprising fact comes in the second half, not all at the start.

SIMPLE ENGLISH, always
- Everyday words a 12-year-old or a non-native speaker understands at first hearing. Short sentences
  (5-12 words), one idea per sentence. Say "use" not "utilize", "help" not "facilitate", "new" not "novel",
  "huge" not "unprecedented", "makes things up" not "hallucinates". No idioms or fancy phrases.
- Technical terms only when needed, explained right away in plain words ("an AI agent - a bot that does tasks for you").
- Banned words: revolutionary, game-changer, groundbreaking, delve, unleash, leverage, ecosystem, paradigm,
  seamless, robust, era, "in the ever-evolving world", "buckle up".

THE ENDING
- Pay off the hook's promise, then end on a line that sticks: a sharp takeaway, a bold (but fair) prediction,
  or a short question that makes people want to comment. No "follow", "like" or "subscribe".

TRUTH (never break this)
- Only use facts from the source. Never invent numbers, quotes, dates, features or results, and never stretch
  them ("this ends X", "everything changes") to sound exciting. Make it exciting through the order, the framing
  and the examples - a hook that isn't true loses trust and viewers."""

SCRIPT_SOURCE = """Use HIS SCRIPT below. Keep his words EXACTLY, in order: do not add, remove or reword anything.
Only split it into scenes and plan the visuals. The hook is his first sentence.
His script:
\"\"\"{script}\"\"\""""

BRIEF_SOURCE = """Write the script yourself from this brief:
\"\"\"{brief}\"\"\""""

FIT_SOURCE = """Base the script on HIS SCRIPT below: keep his message, his key phrases and the order of his points,
but shorten or expand it so it fits the length exactly. Keep his voice; add no facts he didn't give.
If his opening line is weak, sharpen it into a strong hook (using his own facts) - the rest stays his.
His script:
\"\"\"{script}\"\"\""""

# A topic or request ("best video models", "latest iPhone price") instead of a finished script: the model's own
# memory is months/years old, so the facts come from a Google Search first.
RESEARCH_PROMPT = """Today is {today}. Use Google Search to research this for a short video script:
\"\"\"{topic}\"\"\"
Find the CURRENT facts as of today: names, latest versions, numbers, prices, dates. Prefer official sources and the
newest articles. If something changed (a new version, a price cut, a product shut down), report how it is NOW.
Return 6-12 short factual bullet points, each ending with its source and date, like "- Fact. [Publisher, YYYY-MM-DD]".
No intro, no opinions."""

RESEARCH_SOURCE = """Write the script yourself from his request below, using ONLY the facts in the WEB RESEARCH
(Google Search, {today}). Never add facts, numbers, prices, versions or names that are not in the research - your own
memory is out of date. If something isn't covered, leave it out. Keep his angle and any key phrases he used.
His request:
\"\"\"{request}\"\"\"
WEB RESEARCH:
{facts}"""

NORMAL_PROMPT ="""You are the creative director of Master Ashok's (@ashok_a.i) short-form AI videos.
Plan a vertical 9:16 video of about {seconds} seconds made ONLY of AI-generated clips (an image-to-video model,
full screen). There is no presenter, avatar or voice-over; short on-screen text carries the message.

His idea / script:
\"\"\"{brief}\"\"\"

Return ONLY JSON:
{{"title": "<short title>",
  "scenes": [
    {{"caption": "<on-screen text for this clip, 2-7 words, the key point from his script (\\"\\" for none)>",
      "on_screen": "<what this clip shows, one sentence>",
      "visual": "<photorealistic VERTICAL 9:16 image prompt, 25-45 words, the concrete subject, no text>",
      "motion": "<how that image comes alive: camera move + what moves, 10-25 words>",
      "seconds": <whole seconds for this clip, 3-10>}}
  ]}}

Rules:
- Exactly {scenes} scenes whose seconds add up to {seconds}. Follow the order and meaning of his idea/script.
- Visuals must show the actual subject (product, device, place, result), never generic people at laptops.
- A well-known person (e.g. Sam Altman for OpenAI, Elon Musk for xAI / Tesla, Jensen Huang for NVIDIA) may be
  the visual of at most 2 clips, ONLY when the idea is directly about them or a major announcement from the
  company they lead. Realistic editorial press-photo style, never doing anything the idea doesn't say.
- Clip 1 is the hook (the first 2-5 seconds decide if people keep watching): its caption is a curiosity-gap line
  with the most surprising concrete detail (a number, a name, a before/after), e.g. "It wrote 30% of its own code",
  and its visual is the most striking image of the video. Never a dry title like "New AI model released".
- Later captions build to a payoff; the last caption is a sharp takeaway, never "follow for more".
- The first 5 seconds decide if people stay: clips 1-2 must make the viewer curious enough to keep watching.
- Captions in SIMPLE ENGLISH: everyday words anyone understands in one glance, no jargon or fancy words.
  Avoid hype words (revolutionary, game-changer, mind-blowing, incredible).
- Only use facts from his idea/script; make it exciting through framing, never invented numbers or claims."""

IMAGE_PROMPT = """Turn this image request into a prompt for an image model and pick the aspect ratio.
Request: \"\"\"{request}\"\"\"
Keep his subject and every detail he asked for; add composition, lighting and style only where he didn't specify.
Return ONLY JSON: {{"prompt": "<40-80 word image prompt, no text in the image unless he asked for text>",
"aspect": "1:1" | "4:5" | "9:16" | "16:9"}}"""

IMAGES_PROMPT = """Turn this request into {n} prompts for a SET of {n} different images (like an Instagram carousel)
and pick one aspect ratio for all of them.
Request: \"\"\"{request}\"\"\"
Keep his subject and every detail he asked for. Each image shows a different part, angle, step or moment of it
(no near-duplicates), in the same visual style so they belong together. Add composition, lighting and style only
where he didn't specify. Image 1 is the most striking one.
Return ONLY JSON: {{"prompts": ["<40-80 word image prompt, no text in the image unless he asked for text>", ...
exactly {n} prompts], "aspect": "1:1" | "4:5" | "9:16" | "16:9"}}"""
MAX_IMAGES = 6


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
    """Per clip, as billed: one image each, and Wan bills whole seconds (a clone clip is rendered 1 s longer
    than its line). A normal video has no HeyGen part and always uses Wan. A recorded video pays only for its
    photo clips (an image) and video clips (an image + Wan); diagrams, text, logos and his face are free."""
    import math
    secs = max(1, plan.get("duration_sec", 40))
    normal = plan.get("type") == "normal"
    recorded = plan.get("type") == "recorded"
    hg_engine, top = heygen_env()["engine"], video_env()
    engine = "wan" if normal else top["engine"]
    heygen = 0.0 if normal or recorded else round(HEYGEN_PER_MIN.get(hg_engine, 1.0) * secs / 60, 2)
    if recorded:
        kinds = [visual_type(s) for s in plan["scenes"]]
        images = sum(1 for k in kinds if k in ("photo", "video"))
        clip_secs = [max(2, min(15, math.ceil(float(s["seconds"]) + 0.5)))
                     for s, k in zip(plan["scenes"], kinds) if k == "video"]
    else:
        clip_secs = [max(2, min(15, math.ceil(float(s["seconds"]) if normal
                                              else len(s["spoken"].split()) / WORDS_PER_SECOND + 1)))
                     for s in plan["scenes"]]
        images = len(clip_secs)
    rate = {"omni_flash": OMNI_PER_SEC.get(top["resolution"], 0.10),
            "wan": WAN_PER_SEC.get(top["resolution"], 0.025)}.get(engine, 0.0)
    from src.video.slide_visuals import image_price
    content = round(rate * sum(clip_secs) + image_price() * images, 2)
    fx = costs.inr_rate()  # he reads costs in rupees
    return {"heygen_usd": heygen, "content_usd": content, "total_usd": round(heygen + content, 2),
            "heygen_inr": round(heygen * fx), "content_inr": round(content * fx), "total_inr": round((heygen + content) * fx),
            "heygen_engine": None if normal or recorded else hg_engine, "top_engine": engine,
            "top_resolution": top["resolution"], "seconds": secs, "type": plan.get("type", "clone")}


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
"visual": "<photorealistic SQUARE 1:1 image prompt, subject centred, 25-45 words, the concrete subject, no text>",
"motion": "<how that image comes alive as a short video: camera move + what moves, 10-25 words>"}}]}}
Exactly {n} scenes, in order. Visuals must show the actual subject of each clip, never generic people at laptops.
A well-known person (e.g. Sam Altman for OpenAI, Elon Musk for xAI / Tesla, Jensen Huang for NVIDIA) may be the
visual of at most 2 clips, ONLY when the script is directly about them or a major announcement from the company
they lead. Realistic editorial press-photo style, never doing anything the script doesn't say."""


def _length_rules(seconds: int) -> Dict[str, int]:
    """Target words and scene count for a video of `seconds` (one scene per ~6 s, 1-8 scenes)."""
    words = round(seconds * WORDS_PER_SECOND)
    scenes = max(1, min(8, round(seconds / 6)))
    return {"seconds": seconds, "words_lo": round(words * 0.9), "words_hi": round(words * 1.05),
            "scenes": scenes, "per_scene": round(words / scenes)}


DEFAULT_MOTION = "Slow cinematic push-in with subtle natural movement"


def retime(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Start/end of every clip and the total length. Clone: from the words (his pace; synced to his real voice
    after HeyGen renders). Normal: each clip's own seconds (whole seconds, 2-15, what Wan renders)."""
    if plan.get("type") == "recorded":
        return _retime_recorded(plan)
    t = 0.0
    for s in plan["scenes"]:
        if plan.get("type") == "normal":
            s["seconds"] = max(2, min(15, int(round(float(s.get("seconds") or 5)))))
            dur = float(s["seconds"])
        else:
            dur = max(2.0, len(str(s["spoken"]).split()) / WORDS_PER_SECOND)
        s["start"], s["end"] = _fmt(t), _fmt(t + dur)
        t += dur
    plan["duration_sec"] = round(t)
    return plan


def _is_topic(text: str, seconds: int) -> bool:
    """Much shorter than the video needs = a topic/request, not a finished script (the model must add facts)."""
    return len(text.split()) < 0.6 * seconds * WORDS_PER_SECOND


PAGES_PROMPT = """Today is {today}. From ONLY the web pages below, write the CURRENT facts for a short video script about:
\"\"\"{topic}\"\"\"
Names, latest versions, numbers, prices, dates. Where pages disagree, trust the newest page. If something changed
(a new version, a price cut, a product shut down), report how it is NOW. Use only facts stated in the pages.
Return 6-12 short factual bullet points, each ending with its source and date, like "- Fact. [Publisher, YYYY-MM-DD]".
No intro, no opinions.

PAGES:
{pages}"""

_BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0 Safari/537.36"}
_search_skip_until = 0.0  # Google Search grounding out of quota: go straight to the backup for a while


def _google_research(topic: str, today: str) -> Dict[str, Any]:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback

    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2)
    resp = generate_with_fallback(RESEARCH_PROMPT.format(today=today, topic=topic), config, wait=False)
    sources, seen = [], set()
    try:
        for chunk in resp.candidates[0].grounding_metadata.grounding_chunks or []:
            web = chunk.web
            if web and web.uri and (web.title or web.uri) not in seen:
                seen.add(web.title or web.uri)
                sources.append({"title": web.title or "", "url": web.uri})
    except Exception:
        pass
    return {"date": today, "facts": (resp.text or "").strip(), "sources": sources[:8]}


async def _page_text(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())[:3500]
    except Exception:
        return ""


async def _web_research(topic: str, today: str) -> Dict[str, Any]:
    """Backup when Google Search grounding is unavailable: DuckDuckGo results + the top pages' text,
    then Gemini (no search tool) pulls the facts out of those pages only."""
    from bs4 import BeautifulSoup
    from src.google_workspace.agent import generate_with_fallback

    query = f"{topic} {time.strftime('%B %Y')}"
    async with httpx.AsyncClient(headers=_BROWSER, timeout=15, follow_redirects=True) as client:
        r = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
        results = []
        for res in BeautifulSoup(r.text, "html.parser").select(".result"):
            a, snip = res.select_one(".result__a"), res.select_one(".result__snippet")
            href = a.get("href", "") if a else ""
            if href.startswith("http") and "duckduckgo.com" not in href:  # skip ads
                results.append({"title": a.get_text(" ", strip=True), "url": href,
                                "snippet": snip.get_text(" ", strip=True) if snip else ""})
            if len(results) == 6:
                break
        if not results:
            raise RuntimeError("the web search returned no results")
        texts = await asyncio.gather(*[_page_text(client, x["url"]) for x in results[:4]])
    pages = "\n\n".join(f"[{i}] {x['title']} ({x['url']})\n{(texts[i - 1] if i <= 4 else '') or x['snippet']}"
                        for i, x in enumerate(results, 1))
    resp = await asyncio.to_thread(generate_with_fallback, PAGES_PROMPT.format(today=today, topic=topic, pages=pages),
                                   {"temperature": 0.2}, False)
    return {"date": today, "facts": (resp.text or "").strip(),
            "sources": [{"title": x["title"], "url": x["url"]} for x in results]}


async def research(topic: str) -> Dict[str, Any]:
    """Current facts from the web: Google Search grounding first, DuckDuckGo + page reading as the backup.
    Never raises: if both fail, `facts` is empty and `error` says why."""
    global _search_skip_until
    from src import progress
    progress.report("Searching the web for current facts...")
    today = time.strftime("%Y-%m-%d")
    if time.monotonic() >= _search_skip_until:
        try:
            found = await asyncio.to_thread(_google_research, topic, today)
            if found["facts"]:
                return found
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                _search_skip_until = time.monotonic() + 60 * 60
            logger.warning(f"Google Search grounding failed, using the backup search: {str(e)[:160]}")
    try:
        return await _web_research(topic, today)
    except Exception as e:
        logger.warning(f"Video research search failed: {str(e)[:200]}")
        return {"date": today, "facts": "", "sources": [], "error": str(e)[:200]}


async def _plan_normal(brief: str, seconds: int) -> Dict[str, Any]:
    """A no-avatar video: AI clips only, full screen, short on-screen text, clip lengths that add up to `seconds`."""
    n = max(1, min(10, round(seconds / 5)))
    data = await _llm.generate_json(NORMAL_PROMPT.format(brief=brief, seconds=seconds, scenes=n), model=SCRIPT_MODEL)
    scenes = []
    for s in (data.get("scenes") or []):
        if isinstance(s, dict) and s.get("visual"):
            try:
                secs = float(s.get("seconds") or seconds / n)
            except (TypeError, ValueError):
                secs = seconds / n
            scenes.append({"caption": str(s.get("caption") or "").strip(), "on_screen": s.get("on_screen", ""),
                           "visual": s["visual"], "motion": s.get("motion") or DEFAULT_MOTION, "seconds": secs})
    if not scenes:
        raise ValueError("The planner returned no clips")
    scale = seconds / max(1.0, sum(s["seconds"] for s in scenes))  # make the clips add up to his length
    for s in scenes:
        s["seconds"] = s["seconds"] * scale
    return retime({"type": "normal", "title": data.get("title") or "Alfred video",
                   "hook": scenes[0]["caption"] or scenes[0]["on_screen"], "scenes": scenes, "target_sec": seconds})


async def plan_video(brief: str, custom_script: bool = False, seconds: Optional[int] = None,
                     video_type: str = "clone") -> Dict[str, Any]:
    """video_type "clone": him on the bottom (HeyGen) + content on top. With custom_script and no `seconds`
    his words are kept exactly; with `seconds` his script is tightened/expanded to that length.
    video_type "normal": AI clips only (no HeyGen), planned from his text.
    A topic/request (much shorter than the length needs) is researched with Google Search first, so the
    script uses today's facts instead of the model's old memory; the sources go into the plan."""
    target = int(seconds or DEFAULT_SECONDS)
    found = None
    if (seconds or not custom_script or video_type == "normal") and _is_topic(brief, target):
        found = await research(brief)
    plan = await _plan_video(brief, custom_script, seconds, video_type, found)
    if found:
        plan["research"] = {"date": found["date"], "sources": found["sources"], "ok": bool(found["facts"])}
    return plan


async def _plan_video(brief: str, custom_script: bool, seconds: Optional[int], video_type: str,
                      found: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    facts = (found or {}).get("facts")
    if video_type == "normal":
        if facts:
            brief = RESEARCH_SOURCE.format(today=found["date"], request=brief, facts=facts)
        return await _plan_normal(brief, int(seconds or DEFAULT_SECONDS))
    if facts:
        custom_script, brief_source = False, RESEARCH_SOURCE.format(today=found["date"], request=brief, facts=facts)
    elif custom_script and seconds:
        custom_script, brief_source = False, FIT_SOURCE.format(script=brief)
    else:
        brief_source = BRIEF_SOURCE.format(brief=brief)
    if custom_script:
        # His words are split by code and never sent back through the model, so they can't be changed.
        clips = _split_script(brief)
        if not clips:
            raise ValueError("The script is empty")
        data = await _llm.generate_json(VISUALS_PROMPT.format(
            clips="\n".join(f"{i}. {c}" for i, c in enumerate(clips, 1)), n=len(clips)), model=SCRIPT_MODEL)
        planned = [s for s in (data.get("scenes") or []) if isinstance(s, dict)]
        scenes = []
        for i, clip in enumerate(clips):
            s = planned[i] if i < len(planned) else {}
            scenes.append({"spoken": clip, "on_screen": s.get("on_screen", ""),
                           "visual": s.get("visual") or f"Photorealistic wide image representing: {clip}",
                           "motion": s.get("motion") or "Slow cinematic push-in with subtle natural movement"})
        data = {"title": data.get("title"), "hook": clips[0]}
    else:
        data = await _llm.generate_json(PLAN_PROMPT.format(source=brief_source, script_craft=SCRIPT_CRAFT,
                                                           **_length_rules(int(seconds or DEFAULT_SECONDS))),
                                        model=SCRIPT_MODEL)
        scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and s.get("spoken")]
    scenes = _drop_cta(scenes)
    if not scenes:
        raise ValueError("The planner returned no scenes")
    for s in scenes:
        s.setdefault("motion", DEFAULT_MOTION)
    return retime({"type": "clone", "title": data.get("title") or "Alfred video",
                   "hook": data.get("hook") or scenes[0]["spoken"], "scenes": scenes, "target_sec": seconds})


def clean_scenes(plan_type: str, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """His edited clips from the app: trimmed text, required fields checked (raises ValueError)."""
    out = []
    for i, s in enumerate(scenes, 1):
        if not isinstance(s, dict):
            continue
        visual = " ".join(str(s.get("visual") or "").split())
        if plan_type == "recorded":
            out.append(recorded_scene(s, i, strict=True))
            continue
        if plan_type == "normal":
            if not visual:
                raise ValueError(f"Clip {i}: describe what it shows")
            try:
                secs = float(s.get("seconds") or 5)
            except (TypeError, ValueError):
                raise ValueError(f"Clip {i}: the length must be a number of seconds")
            out.append({"caption": " ".join(str(s.get("caption") or "").split()), "visual": visual,
                        "on_screen": str(s.get("on_screen") or ""), "seconds": secs,
                        "motion": " ".join(str(s.get("motion") or "").split()) or DEFAULT_MOTION})
        else:
            spoken = " ".join(str(s.get("spoken") or "").split())
            if not spoken:
                continue  # an emptied clip is removed
            out.append({"spoken": spoken, "on_screen": str(s.get("on_screen") or ""),
                        "visual": visual or f"Photorealistic wide image representing: {spoken}",
                        "motion": " ".join(str(s.get("motion") or "").split()) or DEFAULT_MOTION})
    if not out:
        raise ValueError("The plan needs at least one clip")
    if len(out) > 15:
        raise ValueError("At most 15 clips")
    return out


def plan_markdown(plan: Dict[str, Any]) -> str:
    if plan.get("type") == "recorded":
        lines = [f"**{plan['title']}** (9:16, about {plan['duration_sec']}s)", "",
                 f"**What I understood:** {plan.get('summary', '')}", ""]
        if plan.get("topic") or plan.get("style"):
            lines += [f"**One topic, one look:** {plan.get('topic', '')} · {plan.get('style', '')}", ""]
        lines.append("**Transcript (Telugu):**")
        lines += [f"- {_fmt(float(seg.get('start') or 0))} · {seg.get('text', '')}" for seg in plan.get("transcript", [])]
        kinds = [visual_type(s) for s in plan["scenes"]]
        n_vis = sum(1 for k in kinds if k != "none")
        lines += ["", f"Visuals: {n_vis} of {len(plan['scenes'])} clips show one in a floating window at the top; "
                      "the rest is just you, full screen", ""]
        for i, (s, vt) in enumerate(zip(plan["scenes"], kinds), 1):
            lines.append(f"**Clip {i} ({s['start']}-{s['end']})**")
            if s.get("spoken"):
                lines.append(f"Says: \"{s['spoken']}\"")
            if s.get("meaning"):
                lines.append(f"Meaning: {s['meaning']}")
            lines.append(f"Visual: {VISUAL_LABELS[vt]}")
            if vt in DRAWN_TYPES:
                items = [it["text"] for it in s.get("items", [])]
                body = " → ".join(items) if vt == "diagram" else " · ".join(items)
                lines.append(f"Shows: {s['headline'] + ': ' if s.get('headline') and items else s.get('headline', '')}{body}")
            elif vt != "none":
                lines.append(f"Shows: {s.get('on_screen') or s['visual']}")
            if s.get("why"):
                lines.append(f"Why: {s['why']}")
            lines.append("")
        return "\n".join(lines).strip()
    if plan.get("type") == "normal":
        lines = [f"**{plan['title']}** (9:16 full-screen AI video, about {plan['duration_sec']}s)", "",
                 "No avatar: every clip is generated by the video model (Wan 2.6 Flash)", ""]
        for i, s in enumerate(plan["scenes"], 1):
            lines.append(f"**Clip {i} ({s['start']}-{s['end']}, {s['seconds']}s)**")
            lines.append(f"Shows: {s.get('on_screen') or s['visual']}")
            if s.get("caption"):
                lines.append(f"Text on screen: \"{s['caption']}\"")
            lines.append("")
        return "\n".join(lines + _research_lines(plan)).strip()
    lines = [f"**{plan['title']}** (9:16 split screen, about {plan['duration_sec']}s)", "",
             f"**Hook:** \"{plan['hook']}\"", "",
             "Top half: content video · Bottom half: you explaining (HeyGen)", ""]
    for i, s in enumerate(plan["scenes"], 1):
        lines.append(f"**Clip {i} ({s['start']}-{s['end']})**")
        lines.append(f"Says: \"{s['spoken']}\"")
        lines.append(f"Top shows: {s.get('on_screen', '')}")
        lines.append("")
    return "\n".join(lines + _research_lines(plan)).strip()


def _research_lines(plan: Dict[str, Any]) -> List[str]:
    r = plan.get("research")
    if not r:
        return []
    if not r.get("ok"):
        return ["⚠ I couldn't search the web, so the facts come from the AI's memory and may be out of date. "
                "Check them before you tap Render."]
    lines = [f"**Facts from a web search ({r['date']}):**"]
    lines += [f"- [{s['title'] or 'source'}]({s['url']})" for s in r.get("sources", [])]
    return lines


# ----------------------------------------------------------------------------- images
def _render_image(prompt: str, aspect: str, out: Path, label: str) -> Optional[Path]:
    from src.video.slide_visuals import _render
    return _render(prompt, int(time.time()) % 100000, out, _llm, label=label, aspect=aspect)


async def image_prompt(request: str) -> Dict[str, str]:
    """His request -> the prompt + aspect the image model will get (shown to him before anything is paid)."""
    data = await _llm.generate_json(IMAGE_PROMPT.format(request=request))
    aspect = data.get("aspect") if data.get("aspect") in ("1:1", "4:5", "9:16", "16:9") else "4:5"
    return {"prompt": str(data.get("prompt") or request), "aspect": aspect}


async def image_prompts(request: str, n: int = 1) -> Dict[str, Any]:
    """His request -> `n` prompts (a set of different images) + one aspect, shown to him before anything is paid."""
    n = max(1, min(MAX_IMAGES, n))
    if n == 1:
        p = await image_prompt(request)
        return {"prompts": [p["prompt"]], "aspect": p["aspect"]}
    data = await _llm.generate_json(IMAGES_PROMPT.format(request=request, n=n))
    prompts = [str(p).strip() for p in (data.get("prompts") or []) if str(p).strip()][:n]
    while len(prompts) < n:  # the model returned too few: fill with variations of his request
        prompts.append(f"{request}. Photorealistic, a different angle and moment (image {len(prompts) + 1} of {n}).")
    aspect = data.get("aspect") if data.get("aspect") in ("1:1", "4:5", "9:16", "16:9") else "4:5"
    return {"prompts": prompts, "aspect": aspect}


async def make_images(prompts: List[str], aspect: str = "4:5") -> Dict[str, Any]:
    """Renders his (edited) prompts as a set, in parallel. Raises only if every image failed."""
    prompts = [p for p in prompts if p.strip()][:MAX_IMAGES]
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    ledger = costs.start()
    names = [f"img_{uuid.uuid4().hex[:10]}.png" for _ in prompts]
    paths = await asyncio.gather(*[
        asyncio.to_thread(_render_image, p, aspect, STUDIO_DIR / name, f"image {i} of {len(prompts)}")
        for i, (p, name) in enumerate(zip(prompts, names), 1)])
    urls = [f"/api/v1/studio/files/{name}" for name, path in zip(names, paths) if path]
    if not urls:
        raise RuntimeError("GPT Image 2.5 failed (WaveSpeed balance or network). Please try again in a minute.")
    return {"urls": urls, "failed": len(prompts) - len(urls), "aspect": aspect, "usage": await costs.summary(ledger)}


async def make_image(request: str, aspect: Optional[str] = None, exact_prompt: bool = False) -> Dict[str, Any]:
    """Image from a chat request (enhanced by Gemini) or his own prompt (used as written)."""
    if exact_prompt:
        prompt, aspect = request, aspect or "4:5"
    else:
        p = await image_prompt(request)
        prompt, aspect = p["prompt"], aspect or p["aspect"]
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    name = f"img_{uuid.uuid4().hex[:10]}.png"
    ledger = costs.start()
    path = await asyncio.to_thread(_render_image, prompt, aspect, STUDIO_DIR / name, "your image")
    if not path:
        raise RuntimeError("GPT Image 2.5 failed (WaveSpeed balance or network). Please try again in a minute.")
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


VOICES_FILE = STUDIO_DIR / "voices.json"


async def list_voices(refresh: bool = False) -> List[Dict[str, Any]]:
    """His own HeyGen voices (clones) for the picker shown before rendering; cached for 12 h.
    current = the one used by default (HEYGEN_VOICE_ID, else the one resolve_heygen picked)."""
    items: List[Dict[str, Any]] = []
    try:
        cached = json.loads(VOICES_FILE.read_text(encoding="utf-8"))
        if not refresh and time.time() - cached.get("time", 0) < 12 * 3600:
            items = cached["items"]
    except Exception:
        pass
    if not items:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for v in await _list_all(client, "/v3/voices", {"type": "private"}):
                if not v.get("voice_id"):
                    continue
                preview = v.get("preview_audio_url") or v.get("preview_audio") or next(
                    (val for k, val in v.items() if "preview" in k and isinstance(val, str) and val.startswith("http")), None)
                items.append({"id": v["voice_id"], "name": v.get("name") or "Voice", "language": v.get("language"),
                              "gender": v.get("gender"), "preview": preview})
        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
        VOICES_FILE.write_text(json.dumps({"time": time.time(), "items": items}, indent=1), encoding="utf-8")
    current = heygen_env()["voice_id"]
    if not current:
        try:
            current = (await resolve_heygen())["voice_id"]
        except Exception:
            current = ""
    return [{**v, "current": v["id"] == current} for v in items]


# ----------------------------------------------------------------------------- captions
CAPTION_PROMPT = """Write the post caption for this short vertical video by Master Ashok (@ashok_a.i), for Instagram,
LinkedIn and YouTube Shorts.
Title: {title}
What he says: \"\"\"{script}\"\"\"
Rules: 2-3 short lines in plain English (a hook, then the key point), then 5-8 relevant hashtags on the last line.
At most 2 emojis, no "follow for more", no facts beyond what he says.
Return ONLY JSON: {{"caption": "<the caption>"}}"""


def _plan_text(plan: Dict[str, Any]) -> str:
    """What the video says: his spoken lines, or (normal video) the on-screen text and what the clips show."""
    if plan.get("type") == "recorded":  # his Telugu words + what they mean, so the caption gets it right
        return (plan.get("summary", "") + " " + " ".join(s.get("spoken", "") for s in plan["scenes"])).strip()
    return " ".join(s.get("spoken") or s.get("caption") or s.get("on_screen") or "" for s in plan["scenes"]).strip()


async def make_caption(plan: Dict[str, Any], news_id: Optional[str] = None) -> str:
    """Post caption; for an AI-news video the source (with link) and the publish date are added."""
    script = _plan_text(plan)
    caption = ""
    try:
        data = await _llm.generate_json(CAPTION_PROMPT.format(title=plan.get("title", ""), script=script))
        caption = str(data.get("caption") or "").strip()
    except Exception as e:
        logger.warning(f"Caption generation failed: {e}")
    caption = caption or f"{plan.get('title', '')}\n\n{script}\n\n#AI #AINews #Tech"
    if news_id:
        from src import storage
        from src.models.news import with_source
        item = storage.load_news().get(news_id)
        if item:
            caption = with_source(caption, item.source_note())
    return caption


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
    if job.get("video_type") == "recorded":  # no HeyGen: the top half is most of the work
        pct = 2 + 80 * job.get("_top", 0) + 18 * job.get("_post", 0)
    else:
        pct = 2 + 33 * job.get("_top", 0) + 50 * job.get("_hg", 0) + 15 * job.get("_post", 0)
    job["progress"] = max(job.get("progress", 0), min(99, int(pct)))
    if stage:
        job["stage"] = stage
    _save_job(job)


# ----------------------------------------------------------------------------- HeyGen
async def _heygen_take(client: httpx.AsyncClient, hg: Dict[str, Any], script: str, job: Dict[str, Any]) -> Path:
    """ONE continuous avatar take of the whole script (landscape, filled), downloaded when ready."""
    from src.video.split_video import AVATAR_SPEED
    body = {"type": "avatar", "avatar_id": hg["avatar_id"],
            "engine": {"type": hg["engine"]}, "aspect_ratio": "16:9", "fit": "cover", "resolution": "1080p",
            "title": f"Alfred: {job['plan']['title'][:60]}"}
    # HeyGen always speaks the script itself in his voice: it then knows every sound's timing and the lips
    # match (0 ms). Uploading our own audio instead gave loose lip sync (mouth open in pauses), so never do that.
    body.update(voice_id=hg["voice_id"], script=script, voice_settings={"speed": AVATAR_SPEED})
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


async def _content_sources(job: Dict[str, Any], aspect: Any = "1:1",
                           skip: Optional[set] = None) -> List[Optional[Dict[str, Any]]]:
    """Per scene: a square 1:1 image (the top part is about 1080x1126, so nothing important is cropped), then a content clip from the top engine (or just the image for the free zoom)."""
    from src.video import split_video
    plan, top = job["plan"], video_env()
    sources: List[Optional[Dict[str, Any]]] = []
    n = len(plan["scenes"])
    for i, sc in enumerate(plan["scenes"], 1):
        if skip and i - 1 in skip:  # only his face shows here: no image or clip needed
            sources.append(None)
            continue
        job["message"] = f"Creating image {i} of {len(plan['scenes'])} for the top half..."
        _progress(job, "imagining", top=(i - 1) / n)
        img = await asyncio.to_thread(_render_image, sc["visual"], aspect[i - 1] if isinstance(aspect, list) else aspect,
                                      STUDIO_DIR / f"{job['id']}_scene{i}.png",
                                      f"clip {i} image")
        if not img:
            raise RuntimeError(f"GPT Image 2.5 failed for clip {i}")
        src: Dict[str, Any] = {"image": str(img), "engine": "kenburns"}
        _progress(job, "animating" if top["engine"] in ("wan", "omni_flash") else None,
                  top=(i - 0.5) / n if top["engine"] in ("wan", "omni_flash") else i / n)
        if top["engine"] == "wan":
            job["message"] = f"Animating clip {i} of {len(plan['scenes'])} with Wan 2.6 Flash..."
            _save_job(job)
            seconds = (sc["seconds"] + 0.5 if plan.get("type") == "recorded"  # his clip length, a little longer
                       else len(sc["spoken"].split()) / WORDS_PER_SECOND + 1)  # a little longer than the line; trimmed later
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
    from src import music
    job = _load_jobs()[job_id]
    ledger = costs.start()
    try:
        make = {"normal": _make_normal, "recorded": _make_recorded}.get(job["plan"].get("type"), _make_clone)
        name, total = await make(job)
        caption = await make_caption(job["plan"], job.get("news_id"))  # editable in the app before posting
        job["caption"] = music.with_credit(caption, music.find(job.get("music_id")))
        job.update(status="done", message="Your video is ready.", file=name, url=f"/api/v1/studio/files/{name}",
                   duration=round(total, 1), usage=await costs.summary(ledger), progress=100, stage="done")
        try:
            job["final_copy"] = str(save_final_copy(job, STUDIO_DIR / name))
        except Exception as e:
            logger.warning(f"Could not save the final copy of {job_id}: {e}")
        _save_job(job)
    except asyncio.CancelledError:  # he pressed Cancel during the render
        job.update(status="cancelled", message="Render cancelled. Nothing more will be created; "
                   "anything already started was charged (see below).", usage=await costs.summary(ledger))
        _save_job(job)
    except Exception as e:
        logger.error(f"Studio job {job_id} failed: {e}")
        job.update(status="failed", message=str(e)[:300], usage=await costs.summary(ledger))  # what was spent anyway
        _save_job(job)
    finally:
        _build_tasks.pop(job_id, None)


FINAL_DIR = BASE_DIR / "artifacts" / "final_videos"


def short_name(text: str, words: int = 4, max_len: int = 32) -> str:
    """'OpenAI Makes It Easier for Websites to Block AI Training' -> 'openai_makes_easier_websites'."""
    skip = {"a", "an", "the", "it", "its", "for", "to", "of", "and", "in", "on", "with", "is", "new", "how"}
    parts = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in skip][:words]
    return ("_".join(parts) or "video")[:max_len].rstrip("_")


def save_final_copy(job: Dict[str, Any], video: Path) -> Path:
    """Copy of the finished video in artifacts/final_videos as <news_name>_YYYYMMDDHHMM.mp4
    (the news story's title for news videos, else the video's title)."""
    import shutil
    from src import storage
    item = storage.load_news().get(job.get("news_id") or "")
    name = short_name(item.title if item else job["plan"].get("title", ""))
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    out = FINAL_DIR / f"{name}_{time.strftime('%Y%m%d%H%M')}.mp4"
    shutil.copy2(video, out)
    return out


async def _make_normal(job: Dict[str, Any]) -> tuple:
    """No avatar: per clip a 9:16 image -> Wan 2.6 Flash clip of the clip's length (free zoom if Wan fails),
    then full screen with the on-screen text and optional royalty-free music."""
    from src import music
    from src.video import split_video
    job_id, scenes = job["id"], job["plan"]["scenes"]
    n, res = len(scenes), video_env()["resolution"]
    job.update(status="rendering", message="Creating the images for your video...")
    clips: List[Path] = []
    durations: List[float] = []
    for i, sc in enumerate(scenes, 1):
        job["message"] = f"Creating image {i} of {n}..."
        _progress(job, "imagining", top=(i - 1) / n, hg=(i - 1) / n)  # no HeyGen part: clips fill the bar
        img = await asyncio.to_thread(_render_image, sc["visual"], "9:16", STUDIO_DIR / f"{job_id}_scene{i}.png",
                                      f"clip {i} image")
        if not img:
            raise RuntimeError(f"GPT Image 2.5 failed for clip {i}")
        secs = float(sc["seconds"])
        job["message"] = f"Animating clip {i} of {n} with Wan 2.6 Flash..."
        _progress(job, "animating", top=(i - 0.5) / n, hg=(i - 0.5) / n)
        try:
            clip = await asyncio.to_thread(split_video.wan_clip, img, f"{sc.get('motion', '')}. {sc['visual']}",
                                           secs, STUDIO_DIR / f"{job_id}_clip{i}.mp4", res)
        except Exception as e:
            logger.warning(f"Wan unavailable for clip {i}, using free zoom instead: {e}")
            clip = await asyncio.to_thread(split_video.kenburns_clip, img, secs + 0.5,
                                           STUDIO_DIR / f"{job_id}_kb{i}.mp4", i, split_video.H)
        clips.append(clip)
        durations.append(secs)
        _progress(job, top=i / n, hg=i / n)
    track = music.find(job.get("music_id"))
    audio = await asyncio.to_thread(music.audio_file, track) if track else None
    job["message"] = "Putting the clips together..."
    _progress(job, "stitching", post=0.5)
    name = f"{job_id}.mp4"
    await asyncio.to_thread(split_video.compose_full, clips, durations, STUDIO_DIR / name,
                            STUDIO_DIR / f"{job_id}_work", [s.get("caption", "") for s in scenes], audio)
    return name, sum(durations)


async def _make_clone(job: Dict[str, Any]) -> tuple:
    from src.video import split_video
    job_id = job["id"]
    avatar_task = None
    try:
        hg = await resolve_heygen()  # fail fast (bad key / no avatar) before spending anything
        if job.get("engine"):  # one-off engine for this job (e.g. an Avatar IV test); .env stays as it is
            hg = {**hg, "engine": job["engine"]}
        if job.get("avatar_id") and job["avatar_id"] != hg["avatar_id"]:  # picked in the app for this video
            look = next((a for a in await list_avatars() if a["id"] == job["avatar_id"]), {})
            hg = {**hg, "avatar_id": job["avatar_id"],
                  "voice_id": heygen_env()["voice_id"] or look.get("voice_id") or hg["voice_id"]}
        if job.get("voice_id"):  # the voice he picked in the app wins
            hg = {**hg, "voice_id": job["voice_id"]}
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
        return name, total
    finally:  # cancelled or failed: stop the HeyGen wait too
        if avatar_task and not avatar_task.done():
            avatar_task.cancel()


# ----------------------------------------------------------------------------- his own recording (Telugu)
# "Recorded Vid": he records himself on the phone (usually Telugu); Gemini listens, writes the transcript and what
# it understood, and picks ONE kind of visual per part of his speech. Two looks only: just him full screen (no
# visual), or him with a floating window over the top 40% showing the visual. Nothing paid runs until he taps Render.
VISUAL_TYPES = ("none", "diagram", "text", "logo", "photo", "video")
VISUAL_LABELS = {"none": "Just you", "diagram": "Diagram (free)", "text": "Text (free)", "logo": "Logos (free)",
                 "photo": "Motion photo", "video": "AI video"}
DRAWN_TYPES = ("diagram", "text", "logo")       # drawn and animated on this PC: free
RECORDED_PROMPT = """You are the video editor for Master Ashok (@ashok_a.i), an AI/tech creator. The video is him
talking to his phone's front camera, mostly in Telugu (often with English tech words). It is {secs:.1f} seconds long.
Watch it and listen to it.
The final 9:16 video has two looks: (a) only him, full screen; (b) him in the lower part with a floating window over
the top 40% of the screen that shows a visual about what he is saying right then.

1. Transcribe exactly what he says, in Telugu script (keep the English words he says in English), as timed
   segments of one sentence or phrase each.
2. Say in 2-3 short, simple English sentences what he is talking about: the update or news, and his point.
3. topic: one English line naming the single subject of the whole video (the product / company / news story).
   style: one English line for the look of every AI image in this video (palette, lighting, mood), e.g. "dark
   navy and warm amber, soft studio light, clean modern tech look". All visuals of this video belong to ONE topic
   and ONE look: never show anything unrelated to the topic, never generic stock filler.
4. Split the whole video into {n_min}-{n_max} back-to-back clips that follow his speech (cut where his point
   changes; each clip 3-10 s; the first starts at 0, the last ends at {secs:.1f}).
5. Per clip pick the ONE visual type that helps the viewer most at that moment:
   - "none": only his face. Use for greetings, the opening line where he talks to the camera, talking about
     himself, personal stories, feelings, opinions, jokes, questions to the audience, "follow/comment" lines,
     short transitions, and when he already shows the thing on camera. The first clip is usually "none".
   - "diagram": how something works, a process, steps, cause -> effect, before -> after. 2-4 boxes joined by
     arrows; each box max 5 words.
   - "text": a key number or statistic, a product or feature name, a key term, or a short punchline worth
     reading. headline max 5 words (put the number first if there is one, e.g. "31% fewer tokens"); up to 2
     short lines under it.
   - "logo": he names companies, products or apps with a well-known logo (1-3 of them).
   - "photo": a concrete thing, place, device or scene best shown as a still picture (slow zoom on it).
   - "video": only when motion itself matters (something moving, an action, an event happening). Costs the most:
     at most 2 per video.
   Prefer "diagram", "text" and "logo" (free) whenever they explain the point as well as a picture would.
   Avoid the same visual type more than twice in a row.
6. Fields per clip:
   start (seconds), telugu (his words in it), meaning (one short English line), visual_type, why (one short English
   reason for the choice), on_screen (one short English line: what the viewer sees),
   headline: diagram title (optional, max 5 words) / the text's big headline / logos title (optional, e.g.
     "Built by"); empty for photo, video and none,
   items: diagram boxes / lines under the text / logos, in the order he says them; each {{"text": "<English, max 5
     words>", "at": <second in the video when he says it>}}; a logo item also has "domain" (the brand's official
     website, e.g. "microsoft.com"); empty for photo, video and none,
   visual: for photo and video only, a 40-70 word photorealistic prompt for a landscape 4:3 image that shows what
     he is saying right then, about the video's topic, in the video's style; centred subject, cinematic light; NO
     text, letters, logos or real people's faces in the image,
   motion: for video only, one line of camera/subject motion for an image-to-video model.
Return ONLY JSON: {{"title": "<English title, max 8 words>", "summary": "<2-3 sentences>", "topic": "...",
"style": "...", "transcript": [{{"start": 0.0, "end": 3.2, "text": "<Telugu>"}}],
"clips": [{{"start": 0.0, "telugu": "...", "meaning": "...", "visual_type": "none", "why": "...", "on_screen": "...",
"headline": "", "items": [], "visual": "", "motion": ""}}]}}"""
RECORDED_MODELS = [SCRIPT_MODEL, "gemini-3-flash-preview"]  # listening to Telugu needs the stronger models


def _gemini_media_json(media: bytes, mime: str, prompt: str) -> Dict[str, Any]:
    """Gemini watches/listens to the media and answers in JSON (stronger models first, then the usual fallbacks)."""
    from google.genai import types
    from src.llm.client import available_models, note_model_error
    if not _llm._client:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    models = list(dict.fromkeys(m for m in RECORDED_MODELS + [settings.GEMINI_MODEL, "gemini-3.1-flash-lite"] if m))
    last: Optional[Exception] = None
    for m in available_models(models):
        try:
            r = _llm._client.models.generate_content(
                model=m, contents=[types.Part.from_bytes(data=media, mime_type=mime), prompt],
                config={"response_mime_type": "application/json", "temperature": 0.2})
            if r.text:
                return _llm._extract_json(r.text)
        except Exception as e:
            last = e
            note_model_error(m, e)
            logger.warning(f"Gemini {m} couldn't read the recording: {e}")
    raise last or RuntimeError("Gemini returned nothing for the recording")


def _retime_recorded(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Clips follow his real speech: each lasts until the next one starts; the first starts at 0 and the last ends
    with his recording. Start times are kept in order and at least 1 s apart."""
    total = float(plan["recording_sec"])
    scenes = sorted(plan["scenes"], key=lambda s: float(s.get("start_sec") or 0))
    starts, prev = [], -1.0
    for i, s in enumerate(scenes):
        st = 0.0 if i == 0 else min(max(float(s.get("start_sec") or 0), prev + 1.0), total - 1.0)
        starts.append(round(st, 2))
        prev = st
    for i, s in enumerate(scenes):
        end = starts[i + 1] if i + 1 < len(scenes) else total
        s["start_sec"], s["seconds"] = starts[i], round(end - starts[i], 2)
        s["start"], s["end"] = _fmt(starts[i]), _fmt(end)
    plan["scenes"] = scenes
    plan["duration_sec"] = round(total)
    return plan


def visual_type(scene: Dict[str, Any]) -> str:
    """The clip's visual type (older plans only had needs_visual: those were AI image -> video clips)."""
    t = str(scene.get("visual_type") or "").lower()
    if t in VISUAL_TYPES:
        return t
    return "video" if scene.get("needs_visual", True) else "none"


def _clean_items(items: Any, with_domain: bool) -> List[Dict[str, Any]]:
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        text = " ".join(str(it.get("text") or "").split())[:60]
        if not text:
            continue
        item: Dict[str, Any] = {"text": text}
        try:
            item["at"] = round(float(it["at"]), 2)
        except (KeyError, TypeError, ValueError):
            pass
        if with_domain and str(it.get("domain") or "").strip():
            item["domain"] = str(it["domain"]).strip().lower()[:80]
        out.append(item)
    return out[:4]


def recorded_scene(c: Dict[str, Any], i: int = 1, strict: bool = False) -> Dict[str, Any]:
    """One clip of his recording, from Gemini's answer or his edits. strict (his edits) raises ValueError when a
    picked visual has nothing to show."""
    vt = visual_type(c)
    one = lambda k: " ".join(str(c.get(k) or "").split())  # noqa: E731
    try:
        start = float(c.get("start_sec", c.get("start")) or 0)
    except (TypeError, ValueError):
        raise ValueError(f"Clip {i}: the start must be a number of seconds")
    s = {"spoken": one("spoken") or one("telugu"), "meaning": one("meaning"), "why": one("why"),
         "on_screen": one("on_screen"), "visual_type": vt, "headline": one("headline")[:60],
         "items": _clean_items(c.get("items"), vt == "logo") if vt in DRAWN_TYPES else [],
         "visual": one("visual") if vt in ("photo", "video") else "",
         "motion": (one("motion") or DEFAULT_MOTION) if vt == "video" else "", "start_sec": start}
    s["needs_visual"] = vt != "none"
    if vt in ("photo", "video") and not s["visual"]:
        if strict:
            raise ValueError(f"Clip {i}: describe the picture, or pick another visual")
        s["visual"] = f"Photorealistic image about: {s['meaning'] or s['spoken']}"
    if vt == "text" and not s["headline"]:
        if strict:
            raise ValueError(f"Clip {i}: write the text to show")
        s["headline"] = (s["items"].pop(0)["text"] if s["items"] else s["meaning"][:40]) or "..."
    if vt in ("diagram", "logo") and not s["items"]:
        if strict:
            raise ValueError(f"Clip {i}: add at least one {'box' if vt == 'diagram' else 'logo'}")
        s["items"] = [{"text": s["headline"] or s["meaning"][:40] or "..."}]
    return s


async def start_recorded_job(upload: Path) -> Dict[str, Any]:
    """His uploaded video -> cleaned copy, Telugu transcript, what Alfred understood and a visual per clip.
    The job waits for Render (nothing paid yet; listening with Gemini costs a fraction of a rupee)."""
    from src import progress
    from src.video import split_video as sv
    job_id = uuid.uuid4().hex[:12]
    rec = STUDIO_DIR / f"{job_id}_rec.mp4"
    progress.report("Preparing your video (upright, 25 fps, voice levelled)...")
    await asyncio.to_thread(sv.prepare_recording, upload, rec)
    plan = await plan_recording(rec)
    job = await start_video_job(plan)
    update_job(job, recording=rec.name)
    return job


async def plan_recording(rec: Path) -> Dict[str, Any]:
    """Gemini watches his (cleaned) recording: transcript, summary, one topic and look, and a visual per clip."""
    from src import progress
    from src.video import split_video as sv
    secs = sv.media_duration(rec)
    if secs < 3:
        raise ValueError("The video is shorter than 3 seconds")
    # small copy for Gemini: 360p, 2 frames/s, mono voice (~1-2 MB a minute; Gemini looks at ~1 frame/s anyway)
    small = rec.with_name(rec.stem + "_small.mp4")
    await asyncio.to_thread(sv._run, ["-i", str(rec), "-vf", "scale=-2:360,fps=2", "-c:v", "libx264", "-preset",
                                      "veryfast", "-crf", "32", "-c:a", "aac", "-b:a", "48k", "-ac", "1", str(small)])
    progress.report("Watching and listening to your video (Telugu transcript)...")
    n_min, n_max = max(2, round(secs / 9)), max(3, round(secs / 4))
    try:
        data = await asyncio.to_thread(_gemini_media_json, small.read_bytes(), "video/mp4",
                                       RECORDED_PROMPT.format(secs=secs, n_min=n_min, n_max=n_max))
    finally:
        small.unlink(missing_ok=True)
    progress.report("Picking a visual for each part...")
    clips = [c for c in data.get("clips") or [] if isinstance(c, dict)]
    if not clips:
        raise RuntimeError("Gemini didn't return any clips for the video")
    scenes = [recorded_scene(c, i) for i, c in enumerate(clips[:15], 1)]
    transcript = [{"start": float(s.get("start") or 0), "end": float(s.get("end") or 0),
                   "text": " ".join(str(s.get("text") or "").split())}
                  for s in data.get("transcript") or [] if isinstance(s, dict) and s.get("text")]
    return _retime_recorded({"type": "recorded", "title": str(data.get("title") or "My video")[:100],
                             "summary": str(data.get("summary") or ""),
                             "topic": " ".join(str(data.get("topic") or "").split())[:200],
                             "style": " ".join(str(data.get("style") or "").split())[:200],
                             "transcript": transcript, "recording_sec": round(secs, 2), "scenes": scenes})


def _recorded_prompt(plan: Dict[str, Any], scene: Dict[str, Any]) -> str:
    """The clip's picture prompt tied to the whole video: same topic, same look in every image."""
    parts = [scene["visual"]]
    if plan.get("style"):
        parts.append(f"Visual style shared by every image of this video: {plan['style']}")
    if plan.get("topic"):
        parts.append(f"This image is part of one video about: {plan['topic']}. Show only things related to it")
    return ". ".join(p.rstrip(". ") for p in parts) + "."


async def _make_recorded(job: Dict[str, Any]) -> tuple:
    """His recording + one visual per clip that needs one: diagram / text / logo drawn here (free), photo = AI image
    with a slow zoom, video = AI image -> Wan clip (free zoom if Wan fails). Clips with a visual show it in the
    floating window over the top 40%, the rest show only him (split_video.compose_recorded)."""
    from src.video import recorded_visuals, split_video
    job_id, plan = job["id"], job["plan"]
    scenes = plan["scenes"]
    durations = [float(s["seconds"]) for s in scenes]
    size = (split_video.CARD_W, split_video.CARD_H)
    todo = [i for i, s in enumerate(scenes) if visual_type(s) != "none"]
    clips: List[Optional[Path]] = [None] * len(scenes)
    engines: List[str] = []
    top = video_env()
    if todo:
        job.update(status="rendering", message="Creating the visuals for your video...")
        _progress(job, "imagining")
    for n, i in enumerate(todo):
        s, d, vt = scenes[i], durations[i], visual_type(scenes[i])
        k = i + 1
        job["message"] = f"Making visual {n + 1} of {len(todo)} ({vt})..."
        _progress(job, "imagining", top=n / len(todo))
        if vt in DRAWN_TYPES:
            clips[i] = await asyncio.to_thread(recorded_visuals.render_visual, vt, s, d + 0.5, s["start_sec"], size,
                                               STUDIO_DIR / f"{job_id}_{vt}{k}.mp4", STUDIO_DIR / "logos")
            engines.append(vt)
            continue
        img = await asyncio.to_thread(_render_image, _recorded_prompt(plan, s), "4:3",
                                      STUDIO_DIR / f"{job_id}_scene{k}.png", f"clip {k} image")
        if not img:
            raise RuntimeError(f"GPT Image 2.5 failed for clip {k}")
        clip, engine = None, "kenburns"
        if vt == "video" and top["engine"] in ("wan", "omni_flash"):
            _progress(job, "animating", top=(n + 0.5) / len(todo))
            prompt = f"{s.get('motion') or DEFAULT_MOTION}. {_recorded_prompt(plan, s)}"
            try:
                if top["engine"] == "wan":
                    job["message"] = f"Animating clip {k} with Wan 2.6 Flash..."
                    _save_job(job)
                    clip = await asyncio.to_thread(split_video.wan_clip, img, prompt, d + 0.5,
                                                   STUDIO_DIR / f"{job_id}_clip{k}.mp4", top["resolution"])
                else:
                    job["message"] = f"Animating clip {k} with Gemini Omni Flash..."
                    _save_job(job)
                    clip = await asyncio.to_thread(split_video.omni_clip, img, prompt,
                                                   STUDIO_DIR / f"{job_id}_clip{k}.mp4", top["resolution"])
                    secs = split_video.media_duration(clip)
                    costs.record("Google Gemini", f"Video clip · Omni Flash {top['resolution']}, {secs:.0f}s",
                                 OMNI_PER_SEC.get(top["resolution"], 0.10) * secs)
                engine = top["engine"]
            except Exception as e:  # no key / free seconds used up / error: keep going with the free zoom
                logger.warning(f"{top['engine']} unavailable for clip {k}, using free zoom instead: {e}")
        if clip is None:
            clip = await asyncio.to_thread(split_video.kenburns_clip, img, d + 0.5,
                                           STUDIO_DIR / f"{job_id}_kb{k}.mp4", k,
                                           int(round(split_video.W * size[1] / size[0] / 2)) * 2)
        clips[i] = clip
        engines.append(engine)
    job["top_engines"] = engines
    job.update(status="rendering", message="Putting your video together...")
    _progress(job, "stitching", top=1.0, post=0.3)
    name = f"{job_id}.mp4"
    _progress(job, "finishing", post=0.6)
    await asyncio.to_thread(split_video.compose_recorded, clips, durations, STUDIO_DIR / job["recording"],
                            STUDIO_DIR / name, STUDIO_DIR / f"{job_id}_work")
    return name, sum(durations)


# ----------------------------------------------------------------------------- voice preview
# Optional: he can hear the picked voice BEFORE paying for the video (HeyGen TTS, one file per clip). It is only
# a preview: on Render HeyGen speaks the script itself in that voice, so the lips stay in sync.
VOICE_GAP = 0.25  # s of silence between clips (a natural breath)


async def _tts(client: httpx.AsyncClient, voice_id: str, text: str) -> bytes:
    """HeyGen Voice TTS (works with his own clone) -> WAV bytes."""
    from src.video.split_video import AVATAR_SPEED
    import random
    r = await client.post(f"{HEYGEN}/v3/models/audio/tts", headers=_headers(),
                          json={"voice_id": voice_id, "text": text, "speed": AVATAR_SPEED,
                                "seed": random.randint(1, 10 ** 6)})  # new seed: a redo sounds different
    if r.status_code != 200:
        raise RuntimeError(f"HeyGen voice error {r.status_code}: {r.text[:300]}")
    url = (r.json().get("data") or {}).get("audio_url")
    if not url:
        raise RuntimeError(f"HeyGen returned no audio: {r.text[:200]}")
    return (await client.get(url)).content


def _join_voice(job_id: str, parts: List[Path]) -> Path:
    """Clips -> one 48 kHz WAV with short pauses; sped up 10% only if HeyGen ignored the speed setting."""
    from src.video import split_video as sv
    out = STUDIO_DIR / f"{job_id}_voice.wav"
    inputs, labels = [], []
    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]
        labels.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,apad=pad_dur={VOICE_GAP}[a{i}]")
    chain = ";".join(labels) + ";" + "".join(f"[a{i}]" for i in range(len(parts))) + \
        f"concat=n={len(parts)}:v=0:a=1[v]"
    sv._run([*inputs, "-filter_complex", chain, "-map", "[v]", "-c:a", "pcm_s16le", str(out)])
    return out


async def make_voice(job_id: str, clip: Optional[int] = None, voice_id: Optional[str] = None) -> Dict[str, Any]:
    """Voice preview for every clip (clip=None) or a redo of one clip (1-based), in the voice he picked
    (voice_id; else the job's / the default one). Raises ValueError/KeyError."""
    from src.video import split_video as sv
    job = _load_jobs().get(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] != "awaiting_approval" or job.get("video_type") == "normal":
        raise ValueError("The voice can only be made for a clone video before Render.")
    hg = await resolve_heygen()
    if voice_id:
        job["voice_id"] = voice_id
    hg = {**hg, "voice_id": job.get("voice_id") or hg["voice_id"]}
    scenes = job["plan"]["scenes"]
    voice = job.get("voice") or {}
    files = voice.get("files") or [None] * len(scenes)
    if voice.get("voice_id") != hg["voice_id"]:  # another voice picked: every clip is made again
        files = [None] * len(scenes)
    if clip is None or len(files) != len(scenes) or None in files:
        todo = list(range(len(scenes)))
    elif 1 <= clip <= len(scenes):
        todo = [clip - 1]
    else:
        raise ValueError(f"Clip {clip} doesn't exist")
    stamp = int(time.time())
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        for i in todo:
            path = STUDIO_DIR / f"{job_id}_voice{i + 1}_{stamp}.wav"  # new name: the app never plays a cached old take
            path.write_bytes(await _tts(client, hg["voice_id"], scenes[i]["spoken"]))
            files[i] = path.name
    parts = [STUDIO_DIR / f for f in files]
    joined = await asyncio.to_thread(_join_voice, job_id, parts)
    total = sv.media_duration(joined)
    words = sum(len(s["spoken"].split()) for s in scenes)
    if words / max(0.1, total) < WORDS_PER_SECOND * 0.92:  # the clone ignored the speed: make it 10% faster here
        fast = STUDIO_DIR / f"{job_id}_voice_fast.wav"
        await asyncio.to_thread(sv._run, ["-i", str(joined), "-af", f"atempo={sv.AVATAR_SPEED}", str(fast)])
        fast.replace(joined)
        total = sv.media_duration(joined)
    final = STUDIO_DIR / f"{job_id}_voice_{stamp}.wav"
    joined.replace(final)
    job = _load_jobs()[job_id]  # fresh copy (he may have done something meanwhile)
    job["voice_id"] = hg["voice_id"]
    job["voice"] = {"files": files, "file": final.name, "duration": round(total, 1), "voice_id": hg["voice_id"],
                    "clips": [{"text": s["spoken"], "url": f"/api/v1/studio/files/{f}"} for s, f in zip(scenes, files)],
                    "url": f"/api/v1/studio/files/{final.name}"}
    job["estimate"] = estimate_cost({**job["plan"], "duration_sec": total})
    _save_job(job)
    return {"voice": job["voice"], "estimate": job["estimate"]}


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if k != "plan" and not k.startswith("_")}


async def start_video_job(plan: Dict[str, Any], news_id: Optional[str] = None) -> Dict[str, Any]:
    """Creates the job WAITING FOR APPROVAL: nothing paid runs until he taps Render (approve_job)."""
    job = {"id": uuid.uuid4().hex[:12], "kind": "video", "video_type": plan.get("type", "clone"), "plan": plan,
           "news_id": news_id, "status": "awaiting_approval", "estimate": estimate_cost(plan),
           "message": "Review the plan, then tap Render.", "created": time.time()}
    reason = heygen_ready() if job["video_type"] == "clone" else None
    if reason:
        job.update(status="failed", message=f"HeyGen isn't set up: {reason}.")
    _save_job(job)
    return job


def get_plan(job_id: str) -> Optional[Dict[str, Any]]:
    job = _load_jobs().get(job_id)
    return {"plan": job["plan"], "status": job["status"], "estimate": job.get("estimate")} if job else None


def update_plan(job_id: str, scenes: List[Dict[str, Any]], title: Optional[str] = None,
                style: Optional[str] = None) -> Dict[str, Any]:
    """His edits to the clips (what he says / what's shown / clip length), only before Render.
    Timings and the cost estimate are recalculated. Raises KeyError / ValueError."""
    job = _load_jobs().get(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] != "awaiting_approval":
        raise ValueError("The plan can only be edited before you tap Render.")
    plan = job["plan"]
    plan["scenes"] = clean_scenes(plan.get("type", "clone"), scenes)
    if title and title.strip():
        plan["title"] = title.strip()[:100]
    if style is not None and plan.get("type") == "recorded":
        plan["style"] = " ".join(style.split())[:200]
    if plan.get("type") not in ("normal", "recorded"):
        plan["hook"] = plan["scenes"][0]["spoken"]
    retime(plan)
    if job.get("voice") and [s.get("spoken") for s in plan["scenes"]] != [c["text"] for c in job["voice"]["clips"]]:
        job.pop("voice")  # the words changed: the voice preview has to be made again
    job["estimate"] = estimate_cost(plan)
    job["edited"] = time.time()
    _save_job(job)
    return {"plan": plan, "markdown": plan_markdown(plan), "estimate": job["estimate"], "voice": job.get("voice")}


async def approve_job(job_id: str, avatar_id: Optional[str] = None, music_id: Optional[str] = None,
                      voice_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    job = _load_jobs().get(job_id)
    if not job:
        return None
    if job["status"] == "awaiting_approval":
        if avatar_id:
            job["avatar_id"] = avatar_id
        if voice_id:
            job["voice_id"] = voice_id
            if job.get("voice") and job["voice"].get("voice_id") != voice_id:
                job.pop("voice")  # the preview was another voice: HeyGen speaks the script in the picked voice
        if music_id:
            job["music_id"] = music_id
        if job.get("video_type") == "recorded":
            job["estimate"] = estimate_cost(job["plan"])
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
    print(f"Images: {oi['wavespeed_model']} ({oi['wavespeed_resolution']}, {oi['wavespeed_quality']}, WaveSpeed, "
          f"no fallbacks) · key: {'set' if oi['wavespeed_key'] else 'MISSING (WAVESPEED_API_KEY)'}")
    from src.video.split_video import _dashscope_env
    top, wan = video_env(), _dashscope_env()
    print(f"Top half: {top['engine']} at {top['resolution']}")
    if top["engine"] == "wan":
        if wan["provider"] == "wavespeed":
            print(f"  Wan 2.6 Flash via WaveSpeed AI · key: {'set' if wan['wavespeed_key'] else 'MISSING (WAVESPEED_API_KEY)'}")
        else:
            print(f"  Wan via Alibaba Cloud · key: {'set' if wan['key'] else 'MISSING (DASHSCOPE_API_KEY)'} · {wan['base']}")


def studio_file(name: str) -> Optional[Path]:
    if not re.fullmatch(r"[A-Za-z0-9_]+\.(mp4|png|jpg|wav)", name):
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
