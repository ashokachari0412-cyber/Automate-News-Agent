"""Hands-free voice endpoint for the Alfred mobile app.

Routing (fastest first):
  1. "message/text/SMS <name> saying <text>" -> parsed locally, no LLM call.
  2. Navigation questions                    -> one short LLM call using the Google Maps notification.
  3. Everything else                         -> the same Alfred chatbot (and memory) as the web app.
"""

import logging
import re
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.llm.client import LLMClient

logger = logging.getLogger("alfred_voice")
router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
llm = LLMClient()

VOICE_PERSONA = """You are Alfred, Master Ashok's loyal and caring personal assistant, talking to him through his
Bluetooth headset while he rides his motorbike (not a car) in India.
- Speak natural, warm Indian English, the way a smart, friendly Indian companion talks: simple everyday words,
  relaxed and respectful, a little light humour. Not British, not formal, not robotic.
- Be a real companion in the conversation: react to what he says, and ask a natural follow-up when it helps.
- Your words are spoken aloud: one to three short sentences, no markdown, no lists, no emojis, no URLs.
- Use Indian conventions: kilometres, rupees, IST times.
- Always end every reply with "sir" (e.g. "...right away, sir." or "...shall I, sir?")."""

NAV_PROMPT = VOICE_PERSONA + """
Answer his navigation question using ONLY the Google Maps navigation data below.
Say distances and times naturally ("about 12 kilometres, roughly 18 minutes").
If navigation is not running, say so and offer to help another way."""

SPOKEN_PROMPT = VOICE_PERSONA + """
Below is Alfred's full written answer from the studio. Turn it into what Alfred would SAY aloud:
keep the key point(s), drop formatting, links and long lists (mention the studio has the details if needed)."""

VOICE_HINT = ("\n\n(Master Ashok is speaking to you by voice through his headset while riding his motorbike. "
              "Reply in one to three short spoken sentences in natural, warm Indian English (not British or formal), "
              "no markdown, and end with \"sir\". "
              "You cannot send messages or make calls from here, so never claim you did.)")

NAV_WORDS = re.compile(
    r"\b(km|kms|kilomet\w*|distance|how far|how long|reach|arriv\w*|eta|destination|next turn|turn|"
    r"navigation|route|traffic|minutes left|time left|left to go)\b", re.I)

# "send a message to Vamsi saying ...", "text Vamsi that ...", "whatsapp Vamsi I am late"
MESSAGE_RE = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+)?(?:send\s+)?(?:an?\s+)?"
    r"(?P<channel>sms|text|message|msg|whatsapp)(?:\s+message)?\s+(?:to\s+)?"
    r"(?P<name>[a-z][a-z .']{0,40}?)\s*(?:saying|that says|says|that|stating|telling|:|,)\s+(?P<msg>.+)$",
    re.I)


MESSAGE_START = re.compile(r"^(?:please\s+|can you\s+|could you\s+)?(?:send|text|message|msg|sms|whatsapp)\b", re.I)

EXTRACT_PROMPT = """Extract the messaging command from the user's spoken words. Return ONLY JSON:
{"contact": "<recipient name>", "message": "<message body, faithfully copied, first person>", "channel": "sms" | "whatsapp"}
channel is "whatsapp" only if they said WhatsApp. If it is not a messaging command, return {"contact": "", "message": ""}."""


class NavState(BaseModel):
    title: str = ""
    text: str = ""
    subtext: str = ""
    age_seconds: Optional[int] = None


class VoiceCommand(BaseModel):
    transcript: str
    nav: Optional[NavState] = None
    history: List[Dict[str, str]] = Field(default_factory=list)


class VoiceAction(BaseModel):
    type: Literal["none", "sms", "whatsapp"] = "none"
    contact: str = ""
    message: str = ""


class VoiceReply(BaseModel):
    speak: str
    action: VoiceAction = VoiceAction()
    needs_confirmation: bool = False
    route: str = "chat"


def with_sir(text: str) -> str:
    """Make sure the spoken reply ends with "sir"."""
    text = text.strip()
    if re.search(r"\bsir\W*$", text, re.I):
        return text
    end = "?" if text.endswith("?") else "."
    return re.sub(r"[\s.!?,;:]+$", "", text) + f", sir{end}"


def to_speech(text: str) -> str:
    """Strip markdown / emojis / links so TTS reads it cleanly."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = re.sub(r"[^\w\s.,!?;:'\"()%&@/-]", "", text)  # emojis and symbols
    text = re.sub(r"Master Ashok", "sir", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_message_command(transcript: str) -> Optional[VoiceAction]:
    m = MESSAGE_RE.match(transcript.strip())
    if not m:
        return None
    channel = "whatsapp" if m["channel"].lower() == "whatsapp" or "whatsapp" in transcript.lower() else "sms"
    msg = m["msg"].strip().strip('"').rstrip(".")
    return VoiceAction(type=channel, contact=m["name"].strip().title(), message=msg[:1].upper() + msg[1:])


def _nav_context(nav: Optional[NavState]) -> str:
    if not nav or not (nav.title or nav.text):
        return "Navigation: not running."
    age = f" (updated {nav.age_seconds}s ago)" if nav.age_seconds is not None else ""
    return f"Navigation notification{age}: title='{nav.title}' text='{nav.text}' subtext='{nav.subtext}'"


@router.post("/command", response_model=VoiceReply)
async def voice_command(cmd: VoiceCommand) -> VoiceReply:
    transcript = cmd.transcript.strip()

    # 1. Messaging: no LLM needed. The phone resolves the contact and asks for confirmation.
    action = parse_message_command(transcript)
    if not action and MESSAGE_START.match(transcript):
        try:
            data = await llm.generate_json(f"User said: \"{transcript}\"", EXTRACT_PROMPT)
            if data.get("contact") and data.get("message"):
                action = VoiceAction(type="whatsapp" if data.get("channel") == "whatsapp" else "sms",
                                     contact=str(data["contact"]).title(), message=str(data["message"]))
        except Exception as e:
            logger.warning(f"Message extraction failed: {e}")
        if not action:  # never hand a messaging command to the chatbot: it can't send and might pretend it did
            return VoiceReply(speak="Who shall I message, and what shall I say, sir?", route="message")
    if action:
        speak = f"Send to {action.contact}: {action.message}. Shall I send it, sir?"
        return VoiceReply(speak=speak, action=action, needs_confirmation=True, route="message")

    # 2. Navigation questions answered from the live Google Maps notification.
    if NAV_WORDS.search(transcript):
        history = "\n".join(f"{h.get('role', 'user')}: {h.get('content', '')}" for h in cmd.history[-4:])
        prompt = f"{_nav_context(cmd.nav)}\n\nRecent conversation:\n{history or '(none)'}\n\nMaster Ashok said: \"{transcript}\""
        try:
            reply = await llm.generate_text(prompt, system_prompt=NAV_PROMPT)
            return VoiceReply(speak=with_sir(to_speech(reply)), route="navigation")
        except Exception as e:
            logger.error(f"Voice nav LLM failed: {e}")
            return VoiceReply(speak="My apologies, I can't read the map just now, sir.", route="navigation")

    # 3. Everything else: the same Alfred chatbot the web studio uses (news, carousels, Google, memory...).
    from src.api.main import AlfredChatRequest, run_alfred_chat  # lazy: main imports this module

    try:
        # The voice style hint is kept apart from his words, so it can't influence routing.
        result = await run_alfred_chat(AlfredChatRequest(message=transcript, style_hint=VOICE_HINT))
        reply = result.get("reply", "")
    except Exception as e:
        logger.error(f"Voice chat failed: {e}")
        return VoiceReply(speak="Forgive me, I seem to have lost my train of thought, sir.")

    spoken = to_speech(reply)
    if len(spoken) > 320 or result.get("action"):
        # Long or structured answers (news digests, scripts, carousels): condense for the ear.
        try:
            spoken = to_speech(await llm.generate_text(f"His request: \"{transcript}\"\n\nWritten answer:\n{reply[:6000]}",
                                                       system_prompt=SPOKEN_PROMPT))
        except Exception as e:
            logger.warning(f"Voice condense failed: {e}")
            spoken = spoken[:300].rsplit(".", 1)[0] + ". The rest is waiting in the studio"
    return VoiceReply(speak=with_sir(spoken), route="chat")
