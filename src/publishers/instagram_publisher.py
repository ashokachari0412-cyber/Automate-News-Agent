"""Instagram Publisher — Direct username/password login via instagrapi.

This approach uses instagrapi's built-in login() with username and password,
which handles Instagram's challenge/verification flow properly.
Session is persisted to disk so login only happens once.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
from typing import List

from config.settings import settings

logger = logging.getLogger(__name__)

SESSION_DIR = Path(__file__).resolve().parent.parent.parent / "userdata"
SESSION_FILE = SESSION_DIR / "instagrapi_session.json"


class InstagramPublisher:
    """Publishes Carousels and Reels to Instagram using instagrapi with username/password login."""

    def __init__(self):
        self.username = (getattr(settings, "INSTAGRAM_USERNAME", None) or os.getenv("INSTAGRAM_USERNAME", "")).strip().lstrip("@")
        self.password = (getattr(settings, "INSTAGRAM_PASSWORD", None) or os.getenv("INSTAGRAM_PASSWORD", "")).strip()
        self.session_file = SESSION_FILE
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._client = None

    def is_configured(self) -> bool:
        """Checks if Instagram credentials are provided."""
        return bool(self.username and self.password)

    def _get_client(self):
        """Get authenticated instagrapi client. Reuses saved session if available."""
        if self._client:
            return self._client

        from instagrapi import Client

        cl = Client()
        cl.delay_range = [2, 5]

        # Set device settings to look like a real Android device
        cl.set_settings({
            "device_settings": {
                "app_version": "269.0.0.18.75",
                "android_version": 31,
                "android_release": "12.0",
                "dpi": "480dpi",
                "resolution": "1080x2400",
                "manufacturer": "Samsung",
                "device": "star2qltecs",
                "model": "SM-G965F",
                "cpu": "exynos9810",
                "version_code": "314665256",
            },
            "user_agent": "Instagram 269.0.0.18.75 Android (31/12.0; 480dpi; 1080x2400; Samsung; SM-G965F; star2qltecs; exynos9810; en_US; 314665256)",
        })

        # Try to reuse saved session first
        if self.session_file.exists():
            try:
                logger.info("Loading saved Instagram session...")
                cl.load_settings(self.session_file)
                cl.login(self.username, self.password)
                # Verify session works
                cl.get_timeline_feed()
                logger.info("✅ Instagram session restored successfully!")
                self._client = cl
                return cl
            except Exception as e:
                logger.warning(f"Saved session failed ({e}), doing fresh login...")
                cl = Client()
                cl.delay_range = [2, 5]

        # Fresh login with username/password
        logger.info(f"Logging in to Instagram as @{self.username}...")
        try:
            cl.login(self.username, self.password)
            # Save session for next time
            cl.dump_settings(self.session_file)
            logger.info("✅ Instagram login successful! Session saved.")
            self._client = cl
            return cl
        except Exception as e:
            logger.error(f"Instagram login failed: {e}")
            raise

    async def upload_carousel(self, image_paths: List[Path], caption: str) -> str:
        """Upload multiple images as an Instagram Carousel album."""
        if not self.is_configured():
            raise ValueError("Instagram credentials not configured. Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env")

        valid_paths = [p for p in image_paths if p.exists()]
        if not valid_paths:
            raise FileNotFoundError("No valid image paths found for carousel upload.")

        loop = asyncio.get_running_loop()

        def _sync_upload():
            cl = self._get_client()
            if len(valid_paths) == 1:
                # Albums need 2+ items; a single image is a normal photo post (JPEG required)
                from PIL import Image
                jpg = valid_paths[0].with_suffix(".jpg")
                Image.open(valid_paths[0]).convert("RGB").save(jpg, "JPEG", quality=95)
                logger.info("📤 Uploading single photo post to Instagram...")
                media = cl.photo_upload(jpg, caption=caption)
            else:
                logger.info(f"📤 Uploading {len(valid_paths)} slides as Instagram carousel...")
                media = cl.album_upload(paths=valid_paths, caption=caption)
            code = getattr(media, "code", "")
            url = f"https://www.instagram.com/p/{code}/" if code else f"https://www.instagram.com/{self.username}/"
            logger.info(f"✅ Carousel posted! URL: {url}")
            # Re-save session after successful upload
            try:
                cl.dump_settings(self.session_file)
            except Exception:
                pass
            return url

        return await loop.run_in_executor(self._executor, _sync_upload)

    async def upload_reel(self, video_path: Path, caption: str) -> str:
        """Upload a video Reel to Instagram."""
        if not self.is_configured():
            raise ValueError("Instagram credentials not configured. Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env")

        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found at: {video_path}")

        loop = asyncio.get_running_loop()

        def _sync_upload():
            cl = self._get_client()
            logger.info(f"📤 Uploading reel: {video_path.name}...")
            media = cl.clip_upload(path=video_path, caption=caption)
            code = getattr(media, "code", "")
            url = f"https://www.instagram.com/p/{code}/" if code else f"https://www.instagram.com/{self.username}/reels/"
            logger.info(f"✅ Reel posted! URL: {url}")
            try:
                cl.dump_settings(self.session_file)
            except Exception:
                pass
            return url

        return await loop.run_in_executor(self._executor, _sync_upload)
