"""Gemini agent with Google Workspace function calling.

Uses the google-genai SDK's automatic function calling: Gemini returns a
function_call, the SDK executes the matching Python tool, sends the
function_response back, and repeats until Gemini produces the final answer.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List

from google.genai import types

from config.settings import settings
from src.google_workspace.tools import TOOLS, ui_cards
from src.llm.client import LLMClient

_llm = LLMClient(settings)

SYSTEM_PROMPT = """You are Alfred, Master Ashok's assistant, with read-only access to his Google
Drive, Calendar and Gmail through tools. Current local time: {now}.
- Always call the tools to answer questions about his files, schedule, emails or subscriptions; never invent data.
- Convert relative dates ("today", "tomorrow morning", "yesterday") to exact RFC3339 ranges using the current time and offset.
- For subscriptions, list each recurring service as: service, amount, last billed date, renewal frequency.
- Be concise; use markdown bullets. Download buttons are shown to the user automatically, so don't paste raw URLs."""

KEYWORDS = ["drive", "gdrive", "calendar", "meeting", "schedule", "am i free", "email", "mail",
            "inbox", "gmail", "subscription", "receipt", "invoice", "google photos", "my photos"]


def is_workspace_query(message: str) -> bool:
    m = message.lower()
    return any(k in m for k in KEYWORDS)


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
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=6),
        temperature=0.3,
    )
    response = await asyncio.to_thread(
        _llm._client.models.generate_content, model=settings.GEMINI_MODEL, contents=contents, config=config
    )
    return {"reply": (response.text or "I found nothing for that, sir.").strip(),
            "action": "workspace", "cards": cards}
