"""Publishes to YouTube as a Short: studio videos as they are, carousels as a slideshow.

The YouTube API has no image/community-post upload, so carousel slides are turned into a
vertical 1080x1920 slideshow MP4. Uploads use the YouTube Data API (resumable upload).
Auth reuses the Google OAuth connection (youtube.upload scope).
"""

import asyncio
from pathlib import Path
from typing import List, Optional

import httpx

from config.settings import settings
from src.google_workspace import auth as google_auth

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


class YouTubePublisher:
    def is_configured(self) -> bool:
        return google_auth.has_scope(google_auth.YOUTUBE_UPLOAD_SCOPE)

    async def upload_carousel(self, image_paths: List[Path], caption: str, title: str,
                              audio: Optional[Path] = None) -> str:
        """Slides -> vertical slideshow Short (silent, or with a royalty-free track)."""
        from src import music
        video = await asyncio.to_thread(music.slideshow, image_paths, image_paths[0].parent / "short.mp4", audio)
        return await self.upload_video(video, caption, title)

    async def upload_video(self, video: Path, caption: str, title: str) -> str:
        """Uploads a vertical MP4 as a YouTube Short."""
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
