"""Content-Monitor: what the creators he follows posted in the last 3 days, and which posts broke out.

Uses only Instagram's official Graph API ("Business Discovery"): his Creator account (linked to a Facebook Page)
reads the PUBLIC posts of other Creator/Business accounts. Creators are not notified and nothing is scraped.

Flow: fetch each creator's recent posts -> engagement vs that creator's own usual level (breakout score)
-> Gemini watches the top reels (hook, format, style) -> one brief with "make my version" ideas.

.env (read fresh, no restart):
  META_ACCESS_TOKEN   long-lived token with instagram_basic + pages_show_list (+ pages_read_engagement)
  IG_USER_ID          optional: his Instagram business account id (found automatically from the token)
  META_GRAPH_VERSION  optional, default v23.0
"""

import asyncio
import json
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config.settings import settings
from src import progress
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MONITOR_DIR = BASE_DIR / "artifacts" / "monitor"
CREATORS_FILE = MONITOR_DIR / "creators.json"
LAST_BRIEF_FILE = MONITOR_DIR / "last_brief.json"
ENV_FILE = BASE_DIR / ".env"

DAYS = 3                 # only posts from the last 3 days count
MEDIA_PER_CREATOR = 25   # recent posts fetched per creator (older ones set the creator's usual level)
TOP_POSTS = 8            # posts in the brief
WATCH_VIDEOS = 5         # top reels Gemini watches
MAX_VIDEO_BYTES = 18 * 1024 * 1024  # inline limit for Gemini

DEFAULT_CREATORS = [
    "thevarunmayya", "techplusgadgets", "rrishijain", "tech_iela", "soravjain", "techburner",
    "zainkahn.ai", "therundownai", "matty.mctech", "kallaway", "filatov.design", "raycfu",
]

_llm = LLMClient(settings)

VIDEO_PROMPT = """You are analysing a short-form Instagram reel by @{creator} for a creator who makes AI/tech videos.
Caption: \"\"\"{caption}\"\"\"
Return ONLY JSON:
{{"hook": "<what is said/shown in the first 3 seconds, quoted if spoken>",
  "topic": "<what the video is about, one sentence>",
  "format": "<e.g. talking head, screen recording, split screen, B-roll + voice-over, carousel-style>",
  "style": "<pace, editing, on-screen text, length feel - one sentence>"}}"""

BRIEF_PROMPT = """You are the content strategist for Master Ashok (@ashok_a.i), who posts short AI/tech videos.
Below are the posts that AI/tech creators he follows published in the last {days} days, with engagement.
"breakout" = this post's engagement divided by that creator's usual (median) engagement: 2.0 means twice their normal.

POSTS (best first):
{posts}

CREATORS:
{creators}

Return ONLY JSON:
{{"trends": ["<2-4 topics several creators are covering right now, each with which creators>"],
  "top": [{{"n": <post number from the list>,
           "why": "<why it likely worked: hook, topic timing, format - one sentence>",
           "angle": "<a fresh angle for HIS OWN video on this topic - never a copy of the post>",
           "brief": "<a 1-2 sentence video brief for his planner: the topic + his angle>"}}],
  "patterns": [{{"creator": "<username>", "pattern": "<what they post and how often, one short line>"}}]}}
Rules: "top" covers the {top} best posts in the list. Use only facts from the posts. Simple English."""


# ----------------------------------------------------------------------------- creators list
def load_creators() -> List[str]:
    try:
        data = json.loads(CREATORS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return list(DEFAULT_CREATORS)


def save_creators(handles: List[str]) -> List[str]:
    clean = []
    for h in handles:
        h = h.strip().lstrip("@").split("/")[-1].split("?")[0].lower()
        if re.fullmatch(r"[a-z0-9._]{1,30}", h) and h not in clean:
            clean.append(h)
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    CREATORS_FILE.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


# ----------------------------------------------------------------------------- Graph API
def _graph_settings() -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(ENV_FILE).items()}
    except Exception:
        pass
    return {"token": values.get("META_ACCESS_TOKEN", ""), "ig_user_id": values.get("IG_USER_ID", ""),
            "version": values.get("META_GRAPH_VERSION") or "v23.0"}


def _graph_error(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        return err.get("error_user_msg") or err.get("message") or r.text[:200]
    except Exception:
        return r.text[:200]


async def _my_ig_id(client: httpx.AsyncClient, cfg: Dict[str, str]) -> str:
    """His Instagram business account id: from .env, or found through the Facebook Page linked to it."""
    if cfg["ig_user_id"]:
        return cfg["ig_user_id"]
    r = await client.get(f"https://graph.facebook.com/{cfg['version']}/me/accounts",
                         params={"fields": "instagram_business_account{id,username}", "access_token": cfg["token"]})
    if r.status_code != 200:
        raise RuntimeError(f"Instagram login failed: {_graph_error(r)}")
    for page in r.json().get("data", []):
        ig = page.get("instagram_business_account")
        if ig and ig.get("id"):
            return ig["id"]
    raise RuntimeError("No Instagram Creator account is linked to a Facebook Page on this token.")


async def _fetch_creator(client: httpx.AsyncClient, cfg: Dict[str, str], my_id: str, username: str) -> Dict[str, Any]:
    fields = (f"business_discovery.username({username}){{username,name,followers_count,media_count,"
              f"media.limit({MEDIA_PER_CREATOR}){{id,caption,like_count,comments_count,media_type,"
              f"media_product_type,permalink,timestamp,media_url,thumbnail_url}}}}")
    r = await client.get(f"https://graph.facebook.com/{cfg['version']}/{my_id}",
                         params={"fields": fields, "access_token": cfg["token"]})
    if r.status_code != 200:
        return {"username": username, "error": _graph_error(r)}
    bd = r.json().get("business_discovery", {})
    return {"username": username, "name": bd.get("name") or username, "followers": bd.get("followers_count"),
            "media": (bd.get("media") or {}).get("data", [])}


# ----------------------------------------------------------------------------- scoring
def _engagement(m: Dict[str, Any]) -> Optional[float]:
    """Likes + 2x comments (a comment takes more effort). None when the creator hides likes."""
    if m.get("like_count") is None:
        return None
    return float(m["like_count"]) + 2.0 * float(m.get("comments_count") or 0)


def _score(creators: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    posts = []
    for c in creators:
        if c.get("error"):
            continue
        engs = [e for e in (_engagement(m) for m in c["media"]) if e is not None]
        usual = statistics.median(engs) if engs else None
        for m in c["media"]:
            try:
                when = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            except (KeyError, ValueError):
                continue
            if when < cutoff:
                continue
            eng = _engagement(m)
            age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600
            posts.append({
                "creator": c["username"], "followers": c.get("followers"), "caption": (m.get("caption") or "")[:600],
                "likes": m.get("like_count"), "comments": m.get("comments_count") or 0,
                "type": "reel" if m.get("media_product_type") == "REELS" else (m.get("media_type") or "").lower(),
                "url": m.get("permalink"), "video_url": m.get("media_url") if m.get("media_type") == "VIDEO" else None,
                "age_hours": round(age_h), "posted": when.strftime("%Y-%m-%d"),
                "breakout": round(eng / usual, 2) if eng is not None and usual else None,
            })
    # Breakout first; posts with hidden likes go last (ordered by comments).
    posts.sort(key=lambda p: (p["breakout"] is not None, p["breakout"] or 0, p["comments"]), reverse=True)
    return posts


# ----------------------------------------------------------------------------- watching the reels
def _watch_sync(post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(post["video_url"])
        if r.status_code != 200 or len(r.content) > MAX_VIDEO_BYTES:
            return None
    resp = generate_with_fallback(
        [types.Part.from_bytes(data=r.content, mime_type="video/mp4"),
         VIDEO_PROMPT.format(creator=post["creator"], caption=post["caption"][:400])],
        {"response_mime_type": "application/json", "temperature": 0.2}, False)
    return _llm._extract_json(resp.text or "{}")


async def _watch(post: Dict[str, Any]) -> None:
    try:
        notes = await asyncio.to_thread(_watch_sync, post)
        if notes and not notes.get("error"):
            post["video"] = notes
    except Exception as e:
        logger.warning(f"Could not watch @{post['creator']}'s reel: {str(e)[:160]}")


# ----------------------------------------------------------------------------- the brief
def _post_line(i: int, p: Dict[str, Any]) -> str:
    stats = f"{p['likes']} likes" if p["likes"] is not None else "likes hidden"
    line = (f"{i}. @{p['creator']} ({p['type']}, {p['posted']}, {stats}, {p['comments']} comments, "
            f"breakout {p['breakout'] or 'n/a'}): \"{p['caption'][:300]}\"")
    if p.get("video"):
        v = p["video"]
        line += f"\n   Video - hook: {v.get('hook')} | topic: {v.get('topic')} | format: {v.get('format')} | style: {v.get('style')}"
    return line


def brief_markdown(brief: Dict[str, Any]) -> str:
    # Layout for the web app: whole-line bold headings, "::meta" = small coloured stats line, labels coloured.
    posts, lines = brief["posts"], ["**Content-Monitor**",
                                    f"::meta Last {brief['days']} days · {brief['creators_ok']} of "
                                    f"{brief['creators_total']} creators read", ""]
    if brief.get("trends"):
        lines += ["**Trending now**"] + [f"- {t}" for t in brief["trends"]] + [""]
    if brief.get("top"):
        lines += ["**Breakout posts**", ""]
        for k, t in enumerate(brief["top"], 1):
            p = posts[t["n"] - 1]
            stats = f"{p['likes']:,} likes" if p["likes"] is not None else "likes hidden"
            boost = f" · {p['breakout']}× their usual" if p["breakout"] else ""
            fresh = " · 🆕 still growing" if p["age_hours"] < 24 else ""
            lines += [f"**#{k} @{p['creator']} · {p['type']}**",
                      f"::meta {stats} · {p['comments']:,} comments{boost}{fresh}",
                      f"Hook: {(p.get('video') or {}).get('hook') or p['caption'][:120]}",
                      f"Why it worked: {t.get('why', '')}",
                      f"Your angle: {t.get('angle', '')}",
                      f"[Open post ↗]({p['url']})", ""]
    if brief.get("patterns"):
        lines += ["**What each creator is doing**"] + [f"- @{x['creator']}: {x['pattern']}" for x in brief["patterns"]] + [""]
    if brief.get("errors"):
        lines += ["⚠ Couldn't read: " + ", ".join(f"@{e['username']} ({e['error'][:80]})" for e in brief["errors"])]
    return "\n".join(lines).strip()


async def scan(days: int = DAYS) -> Dict[str, Any]:
    """Raises RuntimeError with a plain-English reason when Instagram isn't connected."""
    cfg = _graph_settings()
    if not cfg["token"]:
        raise RuntimeError("Instagram isn't connected yet: add META_ACCESS_TOKEN to .env (see the setup steps).")
    creators = load_creators()
    if not creators:
        raise RuntimeError("Your creator list is empty: add some handles in Content-Monitor.")
    progress.report(f"Reading the last {days} days of posts from {len(creators)} creators...")
    async with httpx.AsyncClient(timeout=30) as client:
        my_id = await _my_ig_id(client, cfg)
        results = await asyncio.gather(*[_fetch_creator(client, cfg, my_id, u) for u in creators])
    ok = [c for c in results if not c.get("error")]
    errors = [c for c in results if c.get("error")]
    if not ok:
        raise RuntimeError("Couldn't read any creator: " + "; ".join(f"@{e['username']}: {e['error'][:100]}" for e in errors[:3]))

    posts = _score(ok, days)
    if not posts:
        return {"days": days, "posts": [], "creators_ok": len(ok), "creators_total": len(creators), "errors": errors,
                "trends": [], "top": [], "patterns": []}

    reels = [p for p in posts[:TOP_POSTS] if p["video_url"]][:WATCH_VIDEOS]
    if reels:
        progress.report(f"Watching the top {len(reels)} reels...")
        await asyncio.gather(*[_watch(p) for p in reels])

    progress.report("Writing your brief...")
    listed = posts[:30]
    creators_txt = "\n".join(f"@{c['username']}: {c.get('followers') or '?'} followers, "
                             f"{sum(1 for p in posts if p['creator'] == c['username'])} posts in {days} days" for c in ok)
    data = await _llm.generate_json(BRIEF_PROMPT.format(
        days=days, top=min(TOP_POSTS, len(listed)), creators=creators_txt,
        posts="\n".join(_post_line(i, p) for i, p in enumerate(listed, 1))))
    top = [t for t in (data.get("top") or []) if isinstance(t, dict) and isinstance(t.get("n"), int)
           and 1 <= t["n"] <= len(listed)][:TOP_POSTS]
    brief = {"days": days, "posts": listed, "creators_ok": len(ok), "creators_total": len(creators), "errors": errors,
             "trends": [str(t) for t in (data.get("trends") or [])][:4], "top": top,
             "patterns": [x for x in (data.get("patterns") or []) if isinstance(x, dict) and x.get("creator")],
             "scanned": datetime.now(timezone.utc).isoformat()}
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    LAST_BRIEF_FILE.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return brief


def video_brief(brief: Dict[str, Any], k: int) -> str:
    """Brief for the video planner from breakout post #k: his own take, not a copy."""
    if not 1 <= k <= len(brief["top"]):
        raise IndexError(k)
    t = brief["top"][k - 1]
    p = brief["posts"][t["n"] - 1]
    return (f"{t.get('brief') or t.get('angle')}\n(Inspired by a trending post from @{p['creator']}; make an original "
            f"video with his own angle and today's facts - do not copy their script.)")
