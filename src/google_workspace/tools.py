"""Google API tools exposed to Gemini via function calling.

Type hints + docstrings become the tool schema Gemini sees, so keep them precise.
Each tool also pushes UI cards (download buttons, events, ...) into `ui_cards`.
"""

import contextvars
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

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
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,parents)",
    })
    found = data.get("files", []) or _fuzzy_find(keywords, mime)
    files = []
    for f in found:
        info = {
            "name": f["name"], "file_id": f["id"], "mime_type": f["mimeType"],
            "folder_path": _folder_path((f.get("parents") or [None])[0]),
            "size_mb": round(int(f["size"]) / 1e6, 2) if f.get("size") else None,
            "modified": f.get("modifiedTime"), "view_link": f.get("webViewLink"),
            "download_url": f"/api/v1/google/drive/{f['id']}/download",
        }
        files.append(info)
        _card({"type": "file", **info})
    return files


_DRIVE_TYPES = {
    "document": "mimeType = 'application/vnd.google-apps.document'",
    "spreadsheet": "mimeType = 'application/vnd.google-apps.spreadsheet'",
    "presentation": "mimeType = 'application/vnd.google-apps.presentation'",
    "pdf": "mimeType = 'application/pdf'",
    "image": "mimeType contains 'image/'",
    "video": "mimeType contains 'video/'",
    "folder": "mimeType = 'application/vnd.google-apps.folder'",
}


# Short forms people use in file names ("Rel.letter", "adhaar", "pancard", "markslist").
_ALIASES = {
    "relieving": ["rel", "relieve", "relief", "experience"], "aadhaar": ["adhaar", "aadhar", "adhar", "uidai"],
    "aadhar": ["adhaar", "aadhaar", "adhar"], "pan": ["pancard"], "certificate": ["cert", "certificates"],
    "marks": ["markslist", "marksheet", "memo"], "offer": ["appointment"], "salary": ["payslip", "hike", "offer"],
    "resume": ["cv"], "hall": ["ht"], "ticket": ["ht"], "passport": ["pp"],
}


def _fuzzy_find(keywords: str, mime_clause: Optional[str], limit: int = 10) -> List[Dict[str, Any]]:
    """Exact 'name contains' found nothing: score every file name against the keywords, allowing
    misspellings and short forms, and return the best matches."""
    words = [w for w in re.findall(r"[a-z0-9]+", keywords.lower()) if len(w) > 1]
    if not words:
        return []
    variants = {w: [w] + _ALIASES.get(w, []) for w in words}
    q = " and ".join(([mime_clause] if mime_clause else []) + ["trashed = false",
                                                               "mimeType != 'application/vnd.google-apps.folder'"])
    params = {"q": q, "pageSize": 1000, "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,parents)"}
    candidates = _get("https://www.googleapis.com/drive/v3/files", params).get("files", [])

    def score(name: str) -> float:
        tokens = re.findall(r"[a-z0-9]+", name.lower())
        total = 0.0
        for w, alts in variants.items():
            best = 0.0
            for a in alts:
                for t in tokens:
                    if t == a or (len(a) >= 3 and t.startswith(a)):
                        best = max(best, 1.0)
                    elif len(a) >= 4:
                        best = max(best, SequenceMatcher(None, a, t).ratio())
            total += best
        return total / len(variants)

    scored = sorted(((score(f["name"]), f) for f in candidates), key=lambda x: -x[0])
    return [f for s, f in scored if s >= 0.75][:limit]


def list_drive_files(file_type: str = "any", name_contains: str = "", modified_after: str = "",
                     max_results: int = 20) -> Dict[str, Any]:
    """Count and list files in the user's Google Drive (Google Docs, Sheets, Slides, PDFs, images, ...).

    Use for questions like "how many Google Docs do I have", "list my spreadsheets",
    "what files did I change this week" or "show my recent presentations".

    Args:
        file_type: One of "any", "document" (Google Docs), "spreadsheet" (Google Sheets),
            "presentation" (Google Slides), "pdf", "image", "video", "folder".
        name_contains: Optional word that must appear in the file name.
        modified_after: Optional RFC3339 timestamp; only files modified after it.
        max_results: How many of the newest files to list (the total count covers all matches).
    """
    parts = ["trashed = false"]
    if file_type in _DRIVE_TYPES:
        parts.append(_DRIVE_TYPES[file_type])
    if name_contains:
        parts.append(f"name contains '{name_contains.replace(chr(39), '')}'")
    if modified_after:
        parts.append(f"modifiedTime > '{modified_after}'")
    q = " and ".join(parts)

    total, newest, token = 0, [], None
    for _ in range(10):  # counts up to 10,000 files
        params = {"q": q, "pageSize": 1000, "orderBy": "modifiedTime desc",
                  "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink)"}
        if token:
            params["pageToken"] = token
        data = _get("https://www.googleapis.com/drive/v3/files", params)
        files = data.get("files", [])
        total += len(files)
        newest.extend(files[: max(0, max_results - len(newest))])
        token = data.get("nextPageToken")
        if not token:
            break
    return {
        "total_count": total,
        "count_is_capped": bool(token),
        "files": [{"name": f["name"], "file_id": f["id"], "type": f["mimeType"].split(".")[-1],
                   "modified": f.get("modifiedTime"), "view_link": f.get("webViewLink")} for f in newest],
    }


def read_drive_file(file_id: str) -> Dict[str, Any]:
    """Read what is written inside a Drive file: Google Docs, Sheets (as CSV), Slides, text files,
    PDFs (including scanned PDFs) and images/photos of documents (read with OCR).

    Use to summarise, search or answer questions about what a file says (salary, dates, marks, names...).
    Always try this before saying a document can't be read.
    Get the file_id from list_drive_files or search_and_fetch_drive_file first.

    Args:
        file_id: The Drive file id.
    """
    meta = _get(f"https://www.googleapis.com/drive/v3/files/{file_id}", {"fields": "name,mimeType"})
    export = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }.get(meta["mimeType"])
    url = (f"https://www.googleapis.com/drive/v3/files/{file_id}/export" if export
           else f"https://www.googleapis.com/drive/v3/files/{file_id}")
    params = {"mimeType": export} if export else {"alt": "media"}
    mime = meta["mimeType"]
    readable_by_vision = mime == "application/pdf" or mime.startswith("image/")
    if not export and not mime.startswith("text/") and not readable_by_vision:
        return {"name": meta["name"], "error": f"Can't read {mime} files (only Docs, Sheets, Slides, text, PDFs and images)."}
    res = httpx.get(url, params=params, headers={"Authorization": f"Bearer {get_access_token()}"}, timeout=120)
    res.raise_for_status()
    if readable_by_vision:
        # Scanned PDFs and photos of documents have no text layer: let Gemini read them (OCR).
        if len(res.content) > 18_000_000:
            return {"name": meta["name"], "error": "File is too large to read (over 18 MB)."}
        return {"name": meta["name"], "text": _transcribe(res.content, mime)}
    text = res.text
    return {"name": meta["name"], "text": text[:20000], "truncated": len(text) > 20000}


def _transcribe(data: bytes, mime: str) -> str:
    from google.genai import types
    from src.google_workspace.agent import generate_with_fallback
    resp = generate_with_fallback([
        types.Part.from_bytes(data=data, mime_type=mime),
        "Transcribe all the text in this document exactly (keep numbers, dates and amounts as written). "
        "If it is a photo without text, describe what it shows in 2 sentences.",
    ])
    return (resp.text or "")[:20000]


_folder_names: Dict[str, Optional[Dict[str, Any]]] = {}


def _folder_meta(folder_id: str) -> Optional[Dict[str, Any]]:
    if folder_id not in _folder_names:
        try:
            _folder_names[folder_id] = _get(f"https://www.googleapis.com/drive/v3/files/{folder_id}", {"fields": "name,parents"})
        except Exception:
            _folder_names[folder_id] = None
    return _folder_names[folder_id]


def _folder_path(folder_id: Optional[str]) -> str:
    """'My Drive/Documents/Banks' style path for a folder id."""
    parts = []
    for _ in range(8):
        meta = _folder_meta(folder_id) if folder_id else None
        if not meta or not meta.get("parents"):
            parts.append("My Drive")
            break
        parts.append(meta["name"].strip())
        folder_id = meta["parents"][0]
    return "/".join(reversed(parts))


def _kind(mime: str, name: str) -> str:
    if mime == "application/vnd.google-apps.folder":
        return "folder"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "photo"
    if mime == "application/pdf":
        return "pdf"
    if "google-apps" in mime:
        return mime.split(".")[-1]  # document / spreadsheet / presentation
    return name.rsplit(".", 1)[-1].lower() if "." in name else "file"


def explore_drive_folder(folder_name: str) -> Dict[str, Any]:
    """Look inside a Drive folder (and all its subfolders): counts, file types, sizes, upload dates,
    duplicates, largest files and the full file list with each file's subfolder.

    Use for ANY question about what is inside a named folder, e.g. "how many photos are in Chikkamangaluru",
    "what subfolders are in Documents", "largest video in X", "files uploaded on 11 July", "duplicates in X".

    Args:
        folder_name: The folder's name as the user says it (case and extra spaces don't matter).
    """
    wanted = " ".join(folder_name.lower().split())
    q = f"mimeType = 'application/vnd.google-apps.folder' and name contains '{wanted.split()[0].replace(chr(39), '')}' and trashed = false"
    folders = _get("https://www.googleapis.com/drive/v3/files", {"q": q, "pageSize": 50, "fields": "files(id,name)"}).get("files", [])
    folders.sort(key=lambda f: (" ".join(f["name"].lower().split()) != wanted, len(f["name"])))
    if not folders:
        return {"error": f"No folder named like '{folder_name}' was found in Drive."}
    root = folders[0]

    items: List[Dict[str, Any]] = []

    def walk(fid: str, sub: str) -> None:
        token = None
        while True:
            params = {"q": f"'{fid}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken, files(id,name,mimeType,size,createdTime,modifiedTime)"}
            if token:
                params["pageToken"] = token
            data = _get("https://www.googleapis.com/drive/v3/files", params)
            for f in data.get("files", []):
                kind = _kind(f["mimeType"], f["name"])
                items.append({"name": f["name"], "subfolder": sub or "(top level)", "kind": kind,
                              "format": f["name"].rsplit(".", 1)[-1].upper() if "." in f["name"] else kind,
                              "size_mb": round(int(f.get("size", 0)) / 1e6, 1),
                              "uploaded": f["createdTime"][:10], "file_id": f["id"]})
                if kind == "folder":
                    walk(f["id"], f"{sub}/{f['name'].strip()}" if sub else f["name"].strip())
            token = data.get("nextPageToken")
            if not token:
                return

    walk(root["id"], "")
    files = [i for i in items if i["kind"] != "folder"]
    names = Counter(i["name"] for i in files)
    by_sub: Dict[str, Dict[str, Any]] = {}
    for i in files:
        sub = by_sub.setdefault(i["subfolder"], {"total": 0, "unique_names": set(), "by_kind": Counter(),
                                                 "by_format": Counter(), "format_names": {}})
        sub["format_names"].setdefault(i["format"], set()).add(i["name"])
        sub["total"] += 1
        sub["unique_names"].add(i["name"])
        sub["by_kind"][i["kind"]] += 1
        sub["by_format"][i["format"]] += 1
    return {
        "folder": root["name"].strip(),
        "folder_path": _folder_path(root["id"]),
        "subfolders": [i["subfolder"] + "/" + i["name"].strip() if i["subfolder"] != "(top level)" else i["name"].strip()
                       for i in items if i["kind"] == "folder"],
        "total_files": len(files),
        "files_by_kind": dict(Counter(i["kind"] for i in files)),
        "files_by_format": dict(Counter(i["format"] for i in files)),
        # Pre-computed per-subfolder counts (incl. duplicates) so the model never has to count the list itself.
        "files_per_subfolder": {k: {"total": v["total"], "unique": len(v["unique_names"]),
                                    "by_kind": dict(v["by_kind"]), "by_format": dict(v["by_format"]),
                                    "unique_by_format": {f: len(n) for f, n in v["format_names"].items()}}
                                for k, v in by_sub.items()},
        "total_size_gb": round(sum(i["size_mb"] for i in files) / 1000, 2),
        "uploads_per_date": dict(Counter(i["uploaded"] for i in files)),
        "largest_files": sorted(files, key=lambda i: -i["size_mb"])[:5],
        "duplicate_names": {n: c for n, c in names.items() if c > 1},
        "files": [{k: i[k] for k in ("name", "subfolder", "format", "size_mb", "uploaded")} for i in files][:300],
    }


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


TOOLS = [search_and_fetch_drive_file, explore_drive_folder, list_drive_files, read_drive_file, get_calendar_events, search_emails,
         find_active_subscriptions, search_photos]
