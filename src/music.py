"""Background music for image posts: two libraries the app shows as a picker.

- Instagram: tracks from Instagram's own licensed library (styles AI/tech creators use + songs popular in tech
  reels). They are only ever attached INSIDE Instagram (the post becomes a Reel slideshow with the track label),
  never baked into posts for other platforms: Instagram's licence covers Instagram only.
- Royalty-free: Creative Commons "Attribution" music (Kevin MacLeod / incompetech.com and ccMixter), safe on
  every platform. Using one adds the required credit line to the caption.

Both lists are cached (Instagram daily, royalty-free weekly) so the app opens instantly and Instagram isn't
queried on every visit.
"""

import hashlib
import json
import logging
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MUSIC_DIR = BASE_DIR / "artifacts" / "music"
IG_CACHE = MUSIC_DIR / "instagram.json"
RF_CACHE = MUSIC_DIR / "royalty_free.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlfredStudio/1.0"}

# What Instagram is searched for: styles AI/tech creators post with, then songs popular in tech reels.
IG_QUERIES = [
    "tech house", "synthwave", "phonk", "cinematic epic", "future bass", "lofi beats", "dark techno",
    "electronic upbeat", "motivational instrumental", "inspiring corporate", "edm drop", "trap instrumental",
    "Interstellar Hans Zimmer", "Time Hans Zimmer", "Blinding Lights", "Spectre Alan Walker", "Faded Alan Walker",
    "Metamorphosis Interworld", "After Dark Mr Kitty", "Daylight David Kushner", "Unity TheFatRat",
    "Monody TheFatRat", "Believer Imagine Dragons", "Murder In My Mind", "Neon Chase", "Tokyo Drift",
]
IG_PER_QUERY = 3
RF_INCOMPETECH = 35
RF_CCMIXTER = 25
UPBEAT_FEELS = ("driving", "bright", "uplifting", "action", "epic", "intense", "grooving", "bouncy", "mysterious",
                "suspenseful", "hopeful", "determined", "aggressive")


def _fresh(path: Path, max_age: float) -> Optional[List[Dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["items"] if time.time() - data.get("time", 0) < max_age else None
    except Exception:
        return None


def _save(path: Path, items: List[Dict[str, Any]]) -> None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"time": time.time(), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


def _secs(hms: str) -> int:
    parts = [int(p) for p in re.findall(r"\d+", hms or "")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


# ----------------------------------------------------------------------------- royalty-free
def royalty_free(refresh: bool = False) -> List[Dict[str, Any]]:
    """Upbeat / cinematic CC-BY tracks from incompetech.com and ccMixter (cached a week)."""
    cached = None if refresh else _fresh(RF_CACHE, 7 * 86400)
    if cached:
        return cached
    items: List[Dict[str, Any]] = []
    with httpx.Client(timeout=60, headers=UA, follow_redirects=True) as client:
        try:
            pieces = client.get("https://incompetech.com/music/royalty-free/pieces.json").json()
            good = [p for p in pieces if any(f in (p.get("feel") or "").lower() for f in UPBEAT_FEELS)
                    and int(p.get("bpm") or 0) >= 95 and 60 <= _secs(p.get("length")) <= 360]
            good.sort(key=lambda p: p.get("uploaded") or "", reverse=True)  # newest first
            for p in good[:RF_INCOMPETECH]:
                url = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(p["filename"])
                items.append({"id": "rf_" + hashlib.md5(url.encode()).hexdigest()[:12], "source": "incompetech",
                              "title": p["title"], "artist": "Kevin MacLeod", "duration": _secs(p.get("length")),
                              "mood": p.get("feel") or "", "preview_url": url, "audio_url": url,
                              "credit": f'Music: "{p["title"]}" by Kevin MacLeod (incompetech.com), CC BY 4.0'})
        except Exception as e:
            logger.warning(f"incompetech list failed: {e}")
        try:
            res = client.get("https://ccmixter.org/api/query", params={
                "f": "json", "tags": "instrumental,electronic", "lic": "by", "sort": "rank", "limit": RF_CCMIXTER}).json()  # larger pages fail
            for a in res:
                files = [f for f in a.get("files", []) if (f.get("download_url") or "").endswith(".mp3")]
                if not files or len([i for i in items if i["source"] == "ccmixter"]) >= RF_CCMIXTER:
                    continue
                url = files[0]["download_url"]
                artist = a.get("user_real_name") or a.get("user_name") or "ccMixter artist"
                dur = _secs((files[0].get("file_format_info") or {}).get("ps") or "")
                items.append({"id": "rf_" + hashlib.md5(url.encode()).hexdigest()[:12], "source": "ccmixter",
                              "title": a.get("upload_name") or "Untitled", "artist": artist, "duration": dur,
                              "mood": ", ".join((a.get("upload_extra") or {}).get("usertags", "").split(",")[:3]),
                              "preview_url": url, "audio_url": url,
                              "credit": f'Music: "{a.get("upload_name")}" by {artist} (ccmixter.org), CC BY'})
        except Exception as e:
            logger.warning(f"ccMixter list failed: {e}")
    if items:
        _save(RF_CACHE, items)
    return items


# ----------------------------------------------------------------------------- Instagram library
def instagram_tracks(publisher, refresh: bool = False) -> List[Dict[str, Any]]:
    """Instagram's licensed tracks for the picker (cached a day). `publisher` is the InstagramPublisher."""
    cached = None if refresh else _fresh(IG_CACHE, 86400)
    if cached:
        return cached
    if not publisher.is_configured():
        return []
    items: List[Dict[str, Any]] = []
    seen = set()
    for query, track in publisher.search_tracks(IG_QUERIES, IG_PER_QUERY):
        if track.get("is_explicit") or not track.get("uri") or track["id"] in seen:
            continue
        seen.add(track["id"])
        items.append({"id": f"ig_{track['id']}", "source": "instagram", "title": track.get("title") or "Untitled",
                      "artist": track.get("display_artist") or "", "duration": int((track.get("duration_in_ms") or 0) / 1000),
                      "mood": query, "preview_url": track.get("progressive_download_url") or track.get("uri"),
                      "cover": track.get("cover_artwork_thumbnail_uri"), "track": track})
    if items:
        _save(IG_CACHE, items)
    return items


def find(music_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not music_id:
        return None
    for path in (IG_CACHE, RF_CACHE):
        for item in (_fresh(path, 10 ** 9) or []):
            if item["id"] == music_id:
                return item
    return None


def audio_file(item: Dict[str, Any]) -> Path:
    """Local copy of a royalty-free track (downloaded once)."""
    path = MUSIC_DIR / f"{item['id']}.mp3"
    if not path.exists():
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=120, headers=UA, follow_redirects=True) as client:
            path.write_bytes(client.get(item["audio_url"]).content)
    return path


def with_credit(caption: str, item: Optional[Dict[str, Any]]) -> str:
    """CC BY needs the credit line wherever the track is used."""
    return f"{caption}\n\n{item['credit']}" if item and item.get("credit") else caption


# ----------------------------------------------------------------------------- slideshow
def slideshow(image_paths: List[Path], out: Path, audio: Optional[Path] = None, audio_start: float = 0.0,
              seconds_per_image: float = 3.5, min_seconds: float = 8.0) -> Path:
    """Vertical 1080x1920 slideshow of the post images (each on a blurred copy of itself), with optional music
    starting at `audio_start`, faded in and out. Without audio the track is silent."""
    from PIL import Image, ImageFilter, ImageOps
    import imageio_ffmpeg
    work = out.parent / f"{out.stem}_frames"
    work.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(image_paths, 1):
        slide = Image.open(p).convert("RGB")
        bg = ImageOps.fit(slide, (1080, 1920)).filter(ImageFilter.GaussianBlur(40))
        fg = ImageOps.contain(slide, (1080, 1920))
        bg.paste(fg, (0, (1920 - fg.height) // 2))
        bg.save(work / f"frame_{i:02d}.png")
    per = max(seconds_per_image, min_seconds / max(1, len(image_paths)))
    total = per * len(image_paths)
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", f"1/{per}",
           "-i", str(work / "frame_%02d.png")]
    if audio:
        fade_out = max(0.0, total - 1.5)
        cmd += ["-ss", f"{audio_start:.2f}", "-i", str(audio),
                "-af", f"afade=t=in:d=0.8,afade=t=out:st={fade_out:.2f}:d=1.5"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-map", "0:v", "-map", "1:a", "-t", f"{total:.2f}", "-c:v", "libx264", "-r", "30",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
