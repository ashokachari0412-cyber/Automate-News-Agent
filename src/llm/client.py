"""LLM client: Google Gemini first, Groq as the backup provider.

Order for every text / JSON request:
  1. Gemini models (GEMINI_MODEL, then the other Gemini candidates)
  2. Groq models (GROQ_MODELS), only if GROQ_API_KEY is set in .env (read fresh, no restart needed)
  3. Otherwise a clear error / "unavailable" message. There is no offline fake output.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.settings import Settings, settings

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

logger = logging.getLogger(__name__)


class LLMClient:
    """Gemini (primary) + Groq (backup) client used by every text and JSON feature."""

    def __init__(self, cfg: Settings = settings):
        self.cfg = cfg
        self._client = None
        self._init_client()

    def _init_client(self):
        if self.cfg.GEMINI_API_KEY:
            try:
                from google import genai
                clean_key = self.cfg.GEMINI_API_KEY.strip("\"'")
                self._client = genai.Client(api_key=clean_key)
                logger.info(f"Initialized Google Gemini client with model: {self.cfg.GEMINI_MODEL}")
            except Exception as e:
                logger.warning(f"Could not initialize Google GenAI client: {e}")

    async def generate_json(self, prompt: str, system_prompt: str = "") -> Dict[str, Any]:
        """Structured JSON output: Gemini models first, then Groq. Raises if every provider fails."""
        errors = []
        if self._client:
            try:
                return await self._call_gemini_json(prompt, system_prompt)
            except Exception as e:
                errors.append(f"Gemini: {e}")
                logger.error(f"All Gemini models failed for JSON: {e}")
        try:
            return self._extract_json(await self._call_groq(prompt, system_prompt, json_mode=True))
        except Exception as e:
            errors.append(f"Groq: {e}")
        raise RuntimeError("AI providers are unavailable right now (" + " | ".join(e[:160] for e in errors) + ")")

    async def _call_gemini_json(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Invoke Google Gemini model requesting JSON response with automatic model fallback."""
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        candidate_models = [
            self.cfg.GEMINI_MODEL,
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite-preview",
            "gemini-3-flash-preview",
            "gemini-flash-lite-latest",
            "gemini-3.8-flash",
        ]
        candidate_models = list(dict.fromkeys([m for m in candidate_models if m]))

        last_err = None
        for m in candidate_models:
            try:
                response = self._client.models.generate_content(
                    model=m,
                    contents=full_prompt,
                    config={
                        "response_mime_type": "application/json",
                        "temperature": 0.2,
                    }
                )
                text = ""
                try:
                    if hasattr(response, "text") and response.text:
                        text = response.text.strip()
                except Exception:
                    pass
                if not text and getattr(response, "candidates", None) and response.candidates:
                    parts_txt = [p.text for p in response.candidates[0].content.parts if hasattr(p, "text") and p.text]
                    text = "\n".join(parts_txt).strip()
                if text:
                    return self._extract_json(text)
            except Exception as e:
                last_err = e
                logger.warning(f"Gemini model {m} encountered: {e}. Trying next candidate...")

        raise last_err or RuntimeError("All Gemini candidate models failed.")

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        history: Optional[list] = None
    ) -> str:
        """Generate conversational text output using Google Gemini."""
        if self._client:
            candidate_models = [
                self.cfg.GEMINI_MODEL,
                "gemini-3.1-flash-lite",
                "gemini-3.1-flash-lite-preview",
                "gemini-3-flash-preview",
                "gemini-flash-lite-latest",
                "gemini-3.8-flash",
            ]
            candidate_models = list(dict.fromkeys([m for m in candidate_models if m]))

            parts = []
            if system_prompt:
                parts.append(f"Instructions: {system_prompt}\n")
            if history:
                for h in history[-20:]:  # Keep recent context (persistent memory)
                    role = h.get("role", "user")
                    text = h.get("content", "")[:2000]
                    parts.append(f"{role.capitalize()}: {text}")
            parts.append(f"User: {prompt}\nAssistant:")
            full_prompt = "\n".join(parts)

            for m in candidate_models:
                try:
                    response = self._client.models.generate_content(
                        model=m,
                        contents=full_prompt,
                        config={
                            "temperature": 0.7,
                        }
                    )
                    text = ""
                    try:
                        if hasattr(response, "text") and response.text:
                            text = response.text.strip()
                    except Exception:
                        pass
                    if not text and getattr(response, "candidates", None) and response.candidates:
                        parts_txt = [p.text for p in response.candidates[0].content.parts if hasattr(p, "text") and p.text]
                        text = "\n".join(parts_txt).strip()
                    if text:
                        return text
                except Exception as e:
                    logger.warning(f"Gemini chat model {m} encountered: {e}. Trying next...")

        try:
            return await self._call_groq(prompt, system_prompt, history=history)
        except Exception as e:
            logger.warning(f"Groq backup failed: {e}")
        return "I can't reach my AI models right now (Gemini and the Groq backup are both unavailable). Please try again in a minute."

    # ------------------------------------------------------------------ Groq backup
    @staticmethod
    def _groq_settings() -> Dict[str, Any]:
        """Read fresh from .env so adding GROQ_API_KEY works without restarting the server."""
        values: Dict[str, str] = {}
        try:
            from dotenv import dotenv_values
            values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(ENV_FILE).items()}
        except Exception:
            pass
        key = values.get("GROQ_API_KEY") or (settings.GROQ_API_KEY or "").strip()
        models = values.get("GROQ_MODELS") or settings.GROQ_MODELS
        return {"key": key, "models": [m.strip() for m in models.split(",") if m.strip()]}

    async def _call_groq(self, prompt: str, system_prompt: str = "", json_mode: bool = False,
                         history: Optional[list] = None) -> str:
        cfg = self._groq_settings()
        if not cfg["key"]:
            raise RuntimeError("GROQ_API_KEY is not set in .env")
        from groq import Groq
        client = Groq(api_key=cfg["key"])

        messages: List[Dict[str, str]] = []
        if system_prompt or json_mode:
            messages.append({"role": "system", "content": (system_prompt + "\n\nRespond with valid JSON only.").strip()
                             if json_mode else system_prompt})
        for h in (history or [])[-20:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            messages.append({"role": role, "content": h.get("content", "")[:2000]})
        messages.append({"role": "user", "content": prompt})

        last_err: Optional[Exception] = None
        for model in cfg["models"]:
            try:
                kwargs: Dict[str, Any] = {"model": model, "messages": messages,
                                          "temperature": 0.2 if json_mode else 0.7}
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = await asyncio.to_thread(client.chat.completions.create, **kwargs)
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    logger.info(f"Answered by Groq backup model {model}")
                    return text
            except Exception as e:
                last_err = e
                logger.warning(f"Groq model {model} encountered: {e}. Trying next...")
        raise last_err or RuntimeError("All Groq models failed")

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract and parse JSON object from markdown blocks or raw text."""
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except Exception:
            match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    pass
            logger.error(f"Could not parse valid JSON from Gemini output: {text[:200]}")
            return {"error": "Failed to parse JSON", "raw_output": text}
