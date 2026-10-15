"""Publishes image carousels to LinkedIn as a multi-image post (LinkedIn Posts API)."""

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
