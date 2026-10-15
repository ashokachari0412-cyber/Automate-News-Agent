"""Publishes carousels to YouTube as a Short.

The YouTube API has no image/community-post upload, so the slides are turned into a
vertical 1080x1920 slideshow MP4 and uploaded via the YouTube Data API (resumable upload).
Auth reuses the Google OAuth connection (youtube.upload scope).
"""

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import List

import httpx
import imageio_ffmpeg
from PIL import Image, ImageFilter, ImageOps

from config.settings import settings
from src.google_workspace import auth as google_auth

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SECONDS_PER_SLIDE = 3.5


def _build_short(image_paths: List[Path], out_path: Path) -> Path:
    """Places each slide on a blurred 9:16 background and encodes a slideshow MP4."""
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(image_paths, 1):
            slide = Image.open(p).convert("RGB")
            bg = ImageOps.fit(slide, (1080, 1920)).filter(ImageFilter.GaussianBlur(40))
            fg = ImageOps.contain(slide, (1080, 1920))
            bg.paste(fg, (0, (1920 - fg.height) // 2))
            bg.save(Path(tmp) / f"frame_{i:02d}.png")
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-framerate", f"1/{SECONDS_PER_SLIDE}",
            "-i", str(Path(tmp) / "frame_%02d.png"),
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest",
            "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-t", str(SECONDS_PER_SLIDE * len(image_paths)), str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    return out_path


class YouTubePublisher:
    def is_configured(self) -> bool:
        return google_auth.has_scope(google_auth.YOUTUBE_UPLOAD_SCOPE)

    async def upload_carousel(self, image_paths: List[Path], caption: str, title: str) -> str:
        video = await asyncio.to_thread(_build_short, image_paths, image_paths[0].parent / "short.mp4")
        token = google_auth.get_access_token()
        metadata = {
            "snippet": {
                "title": f"{title[:85]} #Shorts",
                "description": caption,
                "categoryId": "28",  # Science & Technology
                "tags": ["AI", "AI news", "tech"],
            },
            "status": {"privacyStatus": settings.YOUTUBE_PRIVACY, "selfDeclaredMadeForKids": False},
        }
        data = video.read_bytes()
        async with httpx.AsyncClient(timeout=300) as client:
            init = await client.post(
                UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=metadata,
                headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                         "X-Upload-Content-Length": str(len(data))},
            )
            if init.status_code >= 400:
                raise RuntimeError(f"YouTube upload init failed ({init.status_code}): {init.text[:300]}")
            res = await client.put(init.headers["Location"], content=data, headers={"Content-Type": "video/mp4"})
            if res.status_code >= 400:
                raise RuntimeError(f"YouTube upload failed ({res.status_code}): {res.text[:300]}")
        return f"https://youtube.com/shorts/{res.json()['id']}"
