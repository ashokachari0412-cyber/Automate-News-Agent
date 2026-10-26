"""Publishes to LinkedIn (Posts API): image carousels as a multi-image post, studio videos as a video post."""

import asyncio
import re
from pathlib import Path
from typing import List

import httpx

from config.settings import settings

API = "https://api.linkedin.com"


def _escape_commentary(text: str) -> str:
    # LinkedIn "little text" format: reserved chars must be escaped (keep '#' so hashtags work).
    return re.sub(r"([\\|{}@\[\]()<>*_~])", r"\\\1", text)


class LinkedInPublisher:
    def is_configured(self) -> bool:
        return bool(settings.LINKEDIN_ACCESS_TOKEN)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}",
            "LinkedIn-Version": settings.LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def _author_urn(self, client: httpx.AsyncClient) -> str:
        if settings.LINKEDIN_AUTHOR_URN:
            return settings.LINKEDIN_AUTHOR_URN
        res = await client.get(f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}"})
        res.raise_for_status()
        return f"urn:li:person:{res.json()['sub']}"

    async def upload_carousel(self, image_paths: List[Path], caption: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            author = await self._author_urn(client)

            async def upload(path: Path) -> str:
                init = await client.post(f"{API}/rest/images?action=initializeUpload", headers=self._headers(),
                                         json={"initializeUploadRequest": {"owner": author}})
                init.raise_for_status()
                value = init.json()["value"]
                put = await client.put(value["uploadUrl"], content=path.read_bytes(),
                                       headers={"Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}"})
                put.raise_for_status()
                return value["image"]

            image_urns = await asyncio.gather(*(upload(p) for p in image_paths))

            post = await client.post(f"{API}/rest/posts", headers=self._headers(), json={
                "author": author,
                "commentary": _escape_commentary(caption),
                "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                 "thirdPartyDistributionChannels": []},
                "content": {"multiImage": {"images": [{"id": urn, "altText": f"Slide {i}"}
                                                      for i, urn in enumerate(image_urns, 1)]}},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            })
            if post.status_code >= 400:
                raise RuntimeError(f"LinkedIn post failed ({post.status_code}): {post.text[:300]}")
            post_urn = post.headers.get("x-restli-id", "")
            return f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else "https://www.linkedin.com/feed/"

    async def upload_video(self, video_path: Path, caption: str, title: str) -> str:
        """Videos API: initialize -> PUT each part -> finalize -> wait until AVAILABLE -> post."""
        from urllib.parse import quote
        data = video_path.read_bytes()
        async with httpx.AsyncClient(timeout=300) as client:
            author = await self._author_urn(client)
            init = await client.post(f"{API}/rest/videos?action=initializeUpload", headers=self._headers(), json={
                "initializeUploadRequest": {"owner": author, "fileSizeBytes": len(data),
                                            "uploadCaptions": False, "uploadThumbnail": False}})
            if init.status_code >= 400:
                raise RuntimeError(f"LinkedIn video upload init failed ({init.status_code}): {init.text[:300]}")
            value = init.json()["value"]
            video_urn = value["video"]
            etags = []
            for part in value["uploadInstructions"]:
                put = await client.put(part["uploadUrl"], content=data[part["firstByte"]:part["lastByte"] + 1],
                                       headers={"Content-Type": "application/octet-stream"})
                if put.status_code >= 400:
                    raise RuntimeError(f"LinkedIn video upload failed ({put.status_code}): {put.text[:300]}")
                etags.append(put.headers.get("etag", ""))
            fin = await client.post(f"{API}/rest/videos?action=finalizeUpload", headers=self._headers(), json={
                "finalizeUploadRequest": {"video": video_urn, "uploadToken": value.get("uploadToken", ""),
                                          "uploadedPartIds": etags}})
            if fin.status_code >= 400:
                raise RuntimeError(f"LinkedIn video finalize failed ({fin.status_code}): {fin.text[:300]}")
            for _ in range(60):  # LinkedIn processes the video before it can be posted (usually < 1 min)
                st = await client.get(f"{API}/rest/videos/{quote(video_urn, safe='')}", headers=self._headers())
                status = (st.json() if st.status_code == 200 else {}).get("status")
                if status == "AVAILABLE":
                    break
                if status == "PROCESSING_FAILED":
                    raise RuntimeError("LinkedIn couldn't process the video")
                await asyncio.sleep(5)
            post = await client.post(f"{API}/rest/posts", headers=self._headers(), json={
                "author": author,
                "commentary": _escape_commentary(caption),
                "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                 "thirdPartyDistributionChannels": []},
                "content": {"media": {"title": title[:200], "id": video_urn}},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            })
            if post.status_code >= 400:
                raise RuntimeError(f"LinkedIn post failed ({post.status_code}): {post.text[:300]}")
            post_urn = post.headers.get("x-restli-id", "")
            return f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else "https://www.linkedin.com/feed/"
