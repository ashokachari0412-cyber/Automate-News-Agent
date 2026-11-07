"""Shared posts: an Instagram / LinkedIn / any link or a screenshot shared to the Alfred app -> a reference brief.

Reads only what is public, the way a chat app builds a link preview:
  1. the link's preview tags (Open Graph / JSON-LD: text, author, image),
  2. if that's thin, Gemini's read-a-link tool,
  3. a shared screenshot, read with Gemini vision (works even when a site hides the post from logged-out readers).
Nothing behind a login is scraped. The brief always asks for HIS OWN take, never a copy of the post.
"""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
SHARES_DIR = BASE_DIR / "artifacts" / "shares"
URL_RE = re.compile(r"https?://[^\s<>\"']+")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}

_llm = LLMClient()

READ_PROMPT = """Master Ashok (@ashok_a.i), an AI/tech content creator, shared a social media post to use as a
REFERENCE for his own content. Everything we could read about it (and any attached images) is below.

{material}

Return ONLY JSON:
{{"ok": <true if there is enough real content to understand what the post says, else false>,
  "author": "<@handle or name of the post's author, or empty>",
  "topic": "<what the post is about, one plain sentence>",
  "key_points": ["<2-4 key facts or ideas the post makes>"],
  "hook": "<the post's opening line / hook if visible, else empty>",
  "format": "<e.g. reel with a talking head, carousel, text post with an image, screenshot of a tweet>"}}
Use only what is in the material and images - never invent facts, numbers or names.
Master Ashok only SHARED it: never name him as the author (unless the post clearly shows his own @ashok_a.i handle),
and his note when sharing is not part of the post (never use it as the hook). If no author is visible, leave it empty."""

LINK_PROMPT = """Open this link and read the social media post: {url}
Return the post's full text/caption exactly, its author, and one sentence describing any image or video.
If you cannot open it or it shows a login page, reply exactly: CANNOT_OPEN"""


# ----------------------------------------------------------------------------- shared screenshots
def save_image(data: bytes, content_type: str = "") -> str:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("The image is empty or larger than 8 MB")
    ext = ".png" if "png" in content_type else ".webp" if "webp" in content_type else ".jpg"
    SHARES_DIR.mkdir(parents=True, exist_ok=True)
    share_id = uuid.uuid4().hex[:12]
    (SHARES_DIR / f"{share_id}{ext}").write_bytes(data)
    return share_id


def _image(share_id: Optional[str]) -> Optional[Tuple[bytes, str]]:
    if not share_id or not re.fullmatch(r"[0-9a-f]{12}", share_id):
        return None
    for path in SHARES_DIR.glob(f"{share_id}.*"):
        mime = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix, "image/jpeg")
        return path.read_bytes(), mime
    return None


# ----------------------------------------------------------------------------- reading a link
def platform_of(url: str) -> str:
    host = re.sub(r"^www\.|^m\.", "", (re.match(r"https?://([^/]+)", url) or [None, ""])[1].lower())
    for key, name in (("instagram.com", "Instagram"), ("linkedin.com", "LinkedIn"), ("lnkd.in", "LinkedIn"),
                      ("x.com", "X"), ("twitter.com", "X"), ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
                      ("threads.net", "Threads"), ("facebook.com", "Facebook")):
        if host.endswith(key):
            return name
    return "Web"


async def _preview(client: httpx.AsyncClient, url: str) -> Dict[str, str]:
    """The link's public preview: Open Graph tags + JSON-LD post text, like a chat app's link preview."""
    from bs4 import BeautifulSoup
    out = {"title": "", "text": "", "image": "", "author": "", "final_url": url}
    try:
        r = await client.get(url)
        out["final_url"] = str(r.url)
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "html.parser")
        meta = {(m.get("property") or m.get("name") or "").lower(): (m.get("content") or "").strip()
                for m in soup.find_all("meta")}
        out["title"] = meta.get("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
        out["text"] = meta.get("og:description") or meta.get("twitter:description") or meta.get("description") or ""
        out["image"] = meta.get("og:image") or meta.get("twitter:image") or ""
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
            except Exception:
                continue
            for d in (data if isinstance(data, list) else [data]):
                if not isinstance(d, dict):
                    continue
                body = d.get("articleBody") or d.get("text") or ""
                if isinstance(body, str) and len(body) > len(out["text"]):
                    out["text"] = body
                author = d.get("author")
                if isinstance(author, dict) and author.get("name") and not out["author"]:
                    out["author"] = str(author["name"])
    except Exception as e:
        logger.info(f"Link preview failed for {url[:80]}: {str(e)[:120]}")
    out["text"] = out["text"][:4000]
    return out


def _read_link_sync(url: str) -> str:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback
    config = types.GenerateContentConfig(tools=[types.Tool(url_context=types.UrlContext())], temperature=0.1)
    text = (generate_with_fallback(LINK_PROMPT.format(url=url), config, wait=False).text or "").strip()
    return "" if "CANNOT_OPEN" in text else text[:4000]


async def _download_image(client: httpx.AsyncClient, url: str) -> Optional[Tuple[bytes, str]]:
    try:
        r = await client.get(url)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") \
                and len(r.content) <= MAX_IMAGE_BYTES:
            return r.content, r.headers["content-type"].split(";")[0]
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------------- understanding it
def _understand_sync(material: str, images: List[Tuple[bytes, str]]) -> Dict[str, Any]:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback
    parts: List[Any] = [types.Part.from_bytes(data=b, mime_type=m) for b, m in images]
    parts.append(READ_PROMPT.format(material=material or "(no text - only the attached image)"))
    resp = generate_with_fallback(parts, {"response_mime_type": "application/json", "temperature": 0.2}, False)
    return _llm._extract_json(resp.text or "{}")


async def read(text: str, share_image: Optional[str] = None) -> Dict[str, Any]:
    """What the shared post is about. Never raises: on failure `ok` is False and `reason` says why."""
    from src import progress
    text = (text or "").strip()
    url_match = URL_RE.search(text)
    url = url_match.group(0).rstrip(").,") if url_match else ""
    note = URL_RE.sub("", text).strip()
    material: List[str] = []
    images: List[Tuple[bytes, str]] = []
    info: Dict[str, Any] = {"url": url, "platform": platform_of(url) if url else "Screenshot"}

    async with httpx.AsyncClient(headers=BROWSER, timeout=20, follow_redirects=True) as client:
        if url:
            progress.report(f"Opening the {info['platform']} post...")
            pv = await _preview(client, url)
            info["url"] = pv["final_url"] or url
            info["platform"] = platform_of(info["url"]) if info["platform"] == "Web" else info["platform"]
            if pv["title"] or pv["text"]:
                material.append(f"Link preview of {info['url']}:\nTitle: {pv['title']}\nText: {pv['text']}"
                                + (f"\nAuthor: {pv['author']}" if pv["author"] else ""))
            if len(pv["text"]) < 80:  # thin preview: let Gemini read the link itself
                progress.report("Reading the post...")
                try:
                    linked = await asyncio.to_thread(_read_link_sync, info["url"])
                    if linked:
                        material.append(f"Post as read from the link:\n{linked}")
                except Exception as e:
                    logger.info(f"Read-link failed: {str(e)[:120]}")
            if pv["image"]:
                img = await _download_image(client, pv["image"])
                if img:
                    images.append(img)
    shot = _image(share_image)
    if shot:
        images.append(shot)
        material.append("A screenshot of the post is attached.")
    if note and note.lower() not in ("check out this post", "check out this reel"):
        material.append(f"His note when sharing (NOT part of the post): {note[:1500]}")

    if not material and not images:
        return {**info, "ok": False, "reason": "nothing to read"}
    progress.report("Understanding the post...")
    try:
        data = await asyncio.to_thread(_understand_sync, "\n\n".join(material), images)
    except Exception as e:
        return {**info, "ok": False, "reason": str(e)[:200]}
    info.update({k: data.get(k) for k in ("author", "topic", "key_points", "hook", "format")})
    info["ok"] = bool(data.get("ok")) and bool(data.get("topic"))
    return info


def brief(info: Dict[str, Any]) -> str:
    """The brief for the image / video planners: the topic and facts as a reference, written as his own take."""
    by = f" by {info['author']}" if info.get("author") else ""
    points = "; ".join(info.get("key_points") or [])
    return (f"Reference post ({info['platform']}{by}): {info.get('topic', '')}\n"
            f"Key points: {points}\n"
            + (f"Their hook: {info['hook']}\n" if info.get("hook") else "")
            + "Make Master Ashok's OWN original content on this topic: his angle, his words and today's facts. "
              "Never copy the post's wording, hook or visuals.")


def reply_markdown(info: Dict[str, Any]) -> str:
    by = f" · {info['author']}" if info.get("author") else ""
    lines = [f"**Shared post · {info['platform']}{by}**"]
    if info.get("format"):
        lines.append(f"::meta {info['format']}")
    lines += ["", f"**About:** {info.get('topic', '')}"]
    lines += [f"• {p}" for p in (info.get("key_points") or [])[:4]]
    if info.get("hook"):
        lines.append(f"Hook: \"{info['hook']}\"")
    if info.get("url"):
        lines.append(f"[Open the post ↗]({info['url']})")
    lines += ["", "What should I make from it? I'll write it in your own words, not theirs."]
    return "\n".join(lines)
