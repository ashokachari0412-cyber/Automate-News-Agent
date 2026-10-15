"""Google API tools exposed to Gemini via function calling.

Type hints + docstrings become the tool schema Gemini sees, so keep them precise.
Each tool also pushes UI cards (download buttons, events, ...) into `ui_cards`.
"""

import contextvars
from typing import Any, Dict, List

import httpx

from src.google_workspace.auth import get_access_token

ui_cards: contextvars.ContextVar[List[Dict[str, Any]]] = contextvars.ContextVar("ui_cards")


def _get(url: str, params: dict = None) -> dict:
    res = httpx.get(url, params=params, headers={"Authorization": f"Bearer {get_access_token()}"}, timeout=30)
    res.raise_for_status()
    return res.json()


def _card(card: Dict[str, Any]) -> None:
    try:
        ui_cards.get().append(card)
    except LookupError:
        pass


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def search_and_fetch_drive_file(keywords: str, file_type: str = "any") -> List[Dict[str, Any]]:
    """Search the user's Google Drive for files and return download links.

    Use for requests like "find my Aadhaar card in Drive" or "get my resume PDF".

    Args:
        keywords: Words that should appear in the file name, e.g. "aadhaar" or "resume 2024".
        file_type: One of "any", "pdf", "image", "document", "spreadsheet".
    """
    terms = [f"name contains '{w.replace(chr(39), '')}'" for w in keywords.split() if w]
    mime = {
        "pdf": "mimeType = 'application/pdf'",
        "image": "mimeType contains 'image/'",
        "document": "(mimeType = 'application/vnd.google-apps.document' or mimeType contains 'word')",
        "spreadsheet": "(mimeType = 'application/vnd.google-apps.spreadsheet' or mimeType contains 'sheet')",
    }.get(file_type)
    q = " and ".join(terms + ([mime] if mime else []) + ["trashed = false"])
    data = _get("https://www.googleapis.com/drive/v3/files", {
        "q": q, "pageSize": 10, "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)",
    })
    files = []
    for f in data.get("files", []):
        info = {
            "name": f["name"], "file_id": f["id"], "mime_type": f["mimeType"],
            "modified": f.get("modifiedTime"), "view_link": f.get("webViewLink"),
            "download_url": f"/api/v1/google/drive/{f['id']}/download",
        }
        files.append(info)
        _card({"type": "file", **info})
    return files


def get_calendar_events(time_min: str, time_max: str, query: str = "") -> List[Dict[str, Any]]:
    """List events from the user's primary Google Calendar in a time range.

    Use for "what's on my calendar today", "am I free tomorrow morning", etc.

    Args:
        time_min: Range start, RFC3339 with timezone offset, e.g. "2026-10-15T00:00:00+05:30".
        time_max: Range end, RFC3339 with timezone offset.
        query: Optional free-text filter on event fields.
    """
    params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
              "orderBy": "startTime", "maxResults": 25}
    if query:
        params["q"] = query
    data = _get("https://www.googleapis.com/calendar/v3/calendars/primary/events", params)
    events = []
    for e in data.get("items", []):
        ev = {
            "title": e.get("summary", "(no title)"),
            "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
            "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
            "meet_link": e.get("hangoutLink"),
            "attendees": [a.get("email") for a in e.get("attendees", [])][:10],
        }
        events.append(ev)
        _card({"type": "event", **ev})
    return events


def _gmail_search(query: str, max_results: int) -> List[Dict[str, Any]]:
    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    ids = _get(base, {"q": query, "maxResults": max_results}).get("messages", [])
    out = []
    for m in ids:
        msg = _get(f"{base}/{m['id']}", [("format", "metadata"), ("metadataHeaders", "Subject"),
                                          ("metadataHeaders", "From"), ("metadataHeaders", "Date")])
        out.append({
            "subject": _header(msg, "Subject"), "from": _header(msg, "From"),
            "date": _header(msg, "Date"), "snippet": msg.get("snippet", ""),
            "link": f"https://mail.google.com/mail/u/0/#inbox/{m['threadId']}",
        })
    return out


def search_emails(gmail_query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Search the user's Gmail using Gmail search syntax and return subject, sender, date and snippet.

    Use for "important emails from yesterday", "emails from my bank", etc.

    Args:
        gmail_query: Gmail search query, e.g. "newer_than:1d is:important" or "from:hdfc newer_than:7d".
        max_results: Maximum emails to return (1-25).
    """
    emails = _gmail_search(gmail_query, max(1, min(max_results, 25)))
    for e in emails:
        _card({"type": "email", **e})
    return emails


def find_active_subscriptions(days: int = 120) -> List[Dict[str, Any]]:
    """Find billing/receipt emails (Google Play, Netflix, AWS, Spotify, SaaS...) to identify active subscriptions.

    Returns raw receipt emails; read them and summarise each recurring service with
    service name, amount, date billed and renewal frequency.

    Args:
        days: How many days back to look (default 120).
    """
    q = (f"newer_than:{days}d subject:(invoice OR receipt OR subscription OR renewed OR renewal "
         f"OR billed OR payment OR \"your order\")")
    return _gmail_search(q, 40)


def search_photos(query: str) -> Dict[str, str]:
    """Search the user's Google Photos library by description or place (e.g. "Chikkamangaluru", "my bike").

    Args:
        query: What to look for in the photos.
    """
    # Google removed library-wide read access (photoslibrary.readonly) from the Photos
    # Library API on 2025-03-31; apps can now only read photos they uploaded themselves
    # or ones the user hand-picks through the Photos Picker API.
    return {"error": "Google no longer allows apps to search a user's whole Google Photos library "
                     "(API restriction since March 2025). Photos can only be shared by picking them "
                     "manually via the Google Photos Picker."}


TOOLS = [search_and_fetch_drive_file, get_calendar_events, search_emails,
         find_active_subscriptions, search_photos]
