"""FastAPI routes for Google account connection and Drive downloads."""

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse

from src.google_workspace import auth

router = APIRouter(prefix="/api/v1/google", tags=["google"])

# Google-native docs have no binary content; export them instead.
EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


@router.get("/status")
async def status():
    return {"configured": auth.is_configured(), "connected": auth.is_connected(),
            "needs_reconnect": auth.needs_reconnect(), "refresh_token_expires_at": auth.refresh_token_expiry()}


@router.get("/connect")
async def connect():
    if not auth.is_configured():
        raise HTTPException(400, "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env")
    return RedirectResponse(auth.build_auth_url())


@router.get("/callback")
async def callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        raise HTTPException(400, f"Google sign-in failed: {error or 'no code'}")
    try:
        auth.exchange_code(code, state)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse("/")


@router.post("/disconnect")
async def disconnect():
    auth.disconnect()
    return {"connected": False}


@router.get("/drive/{file_id}/download")
async def drive_download(file_id: str):
    """Streams a Drive file through the backend so the token never reaches the browser."""
    try:
        token = auth.get_access_token()
    except PermissionError as e:
        raise HTTPException(401, str(e))
    headers = {"Authorization": f"Bearer {token}"}
    base = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(base, params={"fields": "name,mimeType"}, headers=headers)
        if meta.status_code != 200:
            raise HTTPException(meta.status_code, "File not found")
        meta = meta.json()

    name, mime = meta["name"], meta["mimeType"]
    if mime in EXPORTS:
        mime, ext = EXPORTS[mime]
        url, params, name = f"{base}/export", {"mimeType": mime}, name + ext
    else:
        url, params = base, {"alt": "media"}

    async def stream():
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("GET", url, params=params, headers=headers) as res:
                async for chunk in res.aiter_bytes():
                    yield chunk

    safe = name.replace('"', "")
    return StreamingResponse(stream(), media_type=mime,
                             headers={"Content-Disposition": f'attachment; filename="{safe}"'})
