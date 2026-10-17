"""Google OAuth 2.0 (web server flow) with on-disk token storage and auto-refresh.

Single-user local app: tokens live in artifacts/google_token.json (git-ignored).
"""

import json
import logging
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

from config.settings import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TOKEN_FILE = BASE_DIR / "artifacts" / "google_token.json"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

_pending_states: set = set()
_refresh_lock = threading.Lock()  # one refresh at a time (parallel tool calls share the token)
logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def build_auth_url() -> str:
    state = secrets.token_urlsafe(24)
    _pending_states.add(state)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # returns a refresh token
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _save(token: dict) -> None:
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
    if token.get("refresh_token_expires_in"):
        token["refresh_token_expires_at"] = time.time() + int(token["refresh_token_expires_in"])
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token), encoding="utf-8")


def _load() -> Optional[dict]:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return None


def exchange_code(code: str, state: str) -> None:
    if state not in _pending_states:
        raise ValueError("Invalid or expired OAuth state")
    _pending_states.discard(state)
    res = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=20)
    res.raise_for_status()
    token = res.json()
    token["connected_at"] = time.time()
    _save(token)


def get_access_token() -> str:
    """Returns a valid access token, refreshing it if expired. Raises if not connected.

    The connection is only dropped when Google itself revokes the refresh token (invalid_grant);
    network errors / Google outages are retried and never disconnect the account.
    """
    with _refresh_lock:
        token = _load()
        if not token:
            raise PermissionError("Google account not connected")
        if token.get("needs_reconnect"):
            raise PermissionError("Google access was revoked or expired, please reconnect Google")
        if time.time() < token.get("expires_at", 0):
            return token["access_token"]

        last_error = ""
        for attempt in range(3):
            try:
                res = httpx.post(TOKEN_URL, data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": token["refresh_token"],
                    "grant_type": "refresh_token",
                }, timeout=20)
            except httpx.HTTPError as e:
                last_error = str(e)
                time.sleep(2 * (attempt + 1))
                continue
            if res.status_code == 200:
                new = res.json()
                new["refresh_token"] = new.get("refresh_token") or token["refresh_token"]  # usually omitted
                new.setdefault("scope", token.get("scope", ""))
                new["connected_at"] = token.get("connected_at")
                _save(new)
                return new["access_token"]
            if res.status_code in (400, 401) and "invalid_grant" in res.text:
                token["needs_reconnect"] = True  # truly revoked/expired: keep file, flag it for the UI
                TOKEN_FILE.write_text(json.dumps(token), encoding="utf-8")
                logger.warning("Google refresh token was revoked or expired (invalid_grant)")
                raise PermissionError("Google access was revoked or expired, please reconnect Google")
            last_error = f"HTTP {res.status_code}"
            time.sleep(2 * (attempt + 1))
        logger.warning(f"Google token refresh failed temporarily: {last_error}")
        raise PermissionError("Google is temporarily unreachable, please try again in a moment")


def has_scope(scope: str) -> bool:
    token = _load()
    return bool(token and scope in token.get("scope", "").split())


def is_connected() -> bool:
    return TOKEN_FILE.exists()


def needs_reconnect() -> bool:
    token = _load()
    return bool(token and token.get("needs_reconnect"))


def refresh_token_expiry() -> Optional[float]:
    """Epoch time when Google will expire the refresh token (only set for 'Testing' OAuth apps)."""
    token = _load()
    if token and token.get("refresh_token_expires_at"):
        return token["refresh_token_expires_at"]
    return None


def disconnect() -> None:
    token = _load()
    if token:
        try:
            httpx.post(REVOKE_URL, params={"token": token.get("refresh_token") or token["access_token"]}, timeout=10)
        except Exception:
            pass
        TOKEN_FILE.unlink(missing_ok=True)
