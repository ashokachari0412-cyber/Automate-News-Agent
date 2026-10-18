"""Gemini agent with Google Workspace function calling.

Uses the google-genai SDK's automatic function calling: Gemini returns a
function_call, the SDK executes the matching Python tool, sends the
function_response back, and repeats until Gemini produces the final answer.
"""

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Dict, List

from google.genai import types

from config.settings import settings
from src.google_workspace.tools import TOOLS, ui_cards
from src.llm.client import LLMClient

_llm = LLMClient(settings)

SYSTEM_PROMPT = """You are Alfred, Master Ashok's assistant, working like Gemini with his Google apps connected:
read-only access to Google Drive (incl. Docs, Sheets, Slides), Calendar, Gmail and Photos through tools.
Current local time: {now}.
- Always call the tools to answer questions about his files, documents, schedule, emails, photos or subscriptions;
  combine several tools when needed. Never invent data.
- If the tools can't answer (e.g. a Google app you have no access to, like Keep, Contacts or YouTube history),
  say so and explain why: "I don't have access to Google Keep yet, sir."
- Questions that are not about his Google data: answer briefly from general knowledge.
- Folder questions ("in my X folder", "inside X", subfolders, counts, sizes, dates, duplicates): use explore_drive_folder
  and answer from its numbers. "Photos" = photo files (JPG/HEIC/PNG), "videos" = video files (MOV/MP4).
  Take counts from files_per_subfolder / files_by_format (already counted, duplicates included); never count
  the file list yourself. If duplicates exist, add the unique number too ("27 files, 25 unique").
- Questions about what a document SAYS (salary, dates, marks, names): find the file, then read_drive_file it
  (it reads scanned PDFs and photos too) and quote the exact value.
- File names are often abbreviated or misspelled ("adhaar.jpg", "Rel.letter.pdf"). If a search finds nothing,
  try other spellings / short forms, or explore_drive_folder on a likely folder, before saying it isn't there.
- If something really isn't found, say clearly that it isn't in Drive; never guess.
- Convert relative dates ("today", "tomorrow morning", "yesterday") to exact RFC3339 ranges using the current time and offset.
- For subscriptions, list each recurring service as: service, amount, last billed date, renewal frequency.
- Be concise (short answer first, then at most 8 bullets). Download buttons are shown automatically, so don't paste raw URLs."""

KEYWORDS = ["drive", "gdrive", "google docs", "google doc", "my docs", "google sheets", "my sheets",
            "google slides", "my files", "calendar", "meeting", "schedule", "am i free", "email", "mail",
            "inbox", "gmail", "subscription", "receipt", "invoice", "google photos", "my photos"]


def is_workspace_query(message: str) -> bool:
    m = message.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", m) for k in KEYWORDS)


async def run_workspace_agent(message: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
    if not _llm._client:
        return {"reply": "Gemini is not configured (GEMINI_API_KEY missing)."}

    cards: List[Dict[str, Any]] = []
    ui_cards.set(cards)  # copied into the worker thread by asyncio.to_thread

    contents = [
        types.Content(role="model" if h["role"] == "assistant" else "user", parts=[types.Part(text=h["content"][:2000])])
        for h in history[-10:]
    ] + [types.Content(role="user", parts=[types.Part(text=message)])]

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT.format(now=datetime.now().astimezone().isoformat(timespec="minutes")),
        tools=TOOLS,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=8),
        temperature=0.3,
    )
    response = await asyncio.to_thread(generate_with_fallback, contents, config)
    return {"reply": (response.text or "I found nothing for that, sir.").strip(),
            "action": "workspace", "cards": cards}


FALLBACK_MODELS = ["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-flash-lite-latest", "gemini-3.8-flash"]


def generate_with_fallback(contents, config=None):
    """Free-tier quota is per model per minute (HTTP 429): try the next model, and if every model is
    rate limited, wait for the suggested delay once and retry."""
    models = list(dict.fromkeys([settings.GEMINI_MODEL] + FALLBACK_MODELS))
    last_err = None
    for attempt in range(2):
        for model in models:
            try:
                return _llm._client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as e:
                last_err = e
                if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e) and "503" not in str(e):
                    raise
        if attempt == 0:
            m = re.search(r"retryDelay': '(\d+)s", str(last_err))
            time.sleep(min(int(m.group(1)) + 1 if m else 20, 40))
    raise last_err
