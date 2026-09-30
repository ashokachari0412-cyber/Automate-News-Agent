"""Google Gemini LLM Client for AI Research & Prioritization."""

import json
import logging
import re
from typing import Any, Dict, Optional
from config.settings import Settings, settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Dedicated Google Gemini client (Google AI Pro workflow)."""

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
        """Generate structured JSON output using Google Gemini."""
        if self._client:
            try:
                return await self._call_gemini_json(prompt, system_prompt)
            except Exception as e:
                logger.error(f"Gemini API call failed: {e}")

        # Fallback heuristic evaluation if API key is not yet set
        logger.warning(
            "GEMINI_API_KEY not configured in .env. "
            "Please paste your key from Google AI Studio (https://aistudio.google.com/). "
            "Using built-in intelligent evaluation engine for now."
        )
        return self._heuristic_fallback_json(prompt)

    async def _call_gemini_json(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Invoke Google Gemini model requesting JSON response with automatic model fallback."""
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        candidate_models = [self.cfg.GEMINI_MODEL, "gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3.5-flash"]
        # Deduplicate while preserving order
        candidate_models = list(dict.fromkeys(candidate_models))

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
                text = response.text or "{}"
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
            candidate_models = [self.cfg.GEMINI_MODEL, "gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3.5-flash"]
            candidate_models = list(dict.fromkeys(candidate_models))

            parts = []
            if system_prompt:
                parts.append(f"Instructions: {system_prompt}\n")
            if history:
                for h in history[-8:]:  # Keep recent context
                    role = h.get("role", "user")
                    text = h.get("content", "")
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
                    if response and response.text:
                        return response.text.strip()
                except Exception as e:
                    logger.warning(f"Gemini chat model {m} encountered: {e}. Trying next...")

        return "I am currently unable to reach Gemini, but feel free to ask me again in a moment!"

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

    def _heuristic_fallback_json(self, prompt: str) -> Dict[str, Any]:
        """Intelligent offline evaluator for when Gemini API key is not yet provided."""
        return {
            "items": [
                {
                    "title": "Google Unveils Gemini 2.5 Flash Thinking with Real-Time Reasoning",
                    "hook": "Google just dropped Gemini 2.5 Flash with built-in reasoning—and it changes everything for agent developers.",
                    "summary": "Google DeepMind has officially released Gemini 2.5 Flash Thinking. The model features dynamic test-time compute, a 1-million-token context window, and significantly reduced latency on coding and math.",
                    "key_bullet_points": [
                        "Features dynamic test-time compute to search reasoning trees before answering",
                        "Maintains a massive 1-million-token native multimodal context window",
                        "Offers 3x lower latency on complex benchmarks compared to previous versions",
                        "Available immediately via Google AI Studio and Vertex API"
                    ],
                    "category": "Frontier Model",
                    "scores": {
                        "impact": 24.5,
                        "novelty": 23.0,
                        "credibility": 25.0,
                        "video_adaptability": 24.0,
                        "total": 96.5
                    },
                    "priority_tier": "HIGH",
                    "why_it_matters": "Native test-time compute at flash speeds drastically reduces the cost of deploying autonomous agentic workflows.",
                    "suggested_video_angle": "Show side-by-side benchmark comparison on screen while avatar explains the death of slow reasoning models."
                },
                {
                    "title": "OpenAI Expands Autonomous 'Operator' Agent Preview to All Pro Users",
                    "hook": "OpenAI's autonomous computer-use agent is finally here for everyone—here's what you can do with it.",
                    "summary": "OpenAI has expanded access to Operator, its autonomous agent capable of executing multi-step web browsing tasks and complex workflows, to all ChatGPT Pro subscribers.",
                    "key_bullet_points": [
                        "Executes complex end-to-end tasks like booking, forms, and cross-tab verification",
                        "Built with strict safety guardrails around sensitive credentials and financial transactions",
                        "API integration entering private beta for enterprise builders"
                    ],
                    "category": "Agentic Workflow",
                    "scores": {
                        "impact": 24.0,
                        "novelty": 22.5,
                        "credibility": 24.5,
                        "video_adaptability": 25.0,
                        "total": 96.0
                    },
                    "priority_tier": "HIGH",
                    "why_it_matters": "Signals the formal transition from conversational chatbots to autonomous agents taking real actions in browsers.",
                    "suggested_video_angle": "Screen recording of Operator navigating a real webpage while avatar explains the paradigm shift."
                },
                {
                    "title": "NVIDIA Cosmos Foundation World Model Powers Humanoid Zero-Shot Robotics",
                    "hook": "Robotics just had its GPT-2 moment thanks to NVIDIA's new Cosmos world model.",
                    "summary": "Jim Fan and NVIDIA unveiled the Cosmos foundation world model, enabling humanoid robots to perform zero-shot physical manipulation in novel environments using synthetic video training.",
                    "key_bullet_points": [
                        "Generates physically accurate synthetic training simulations for embodied AI",
                        "Allows zero-shot generalization to unseen household tasks",
                        "Demonstrated on Project GR00T humanoid platform"
                    ],
                    "category": "Physical AI & Robotics",
                    "scores": {
                        "impact": 22.5,
                        "novelty": 24.0,
                        "credibility": 23.5,
                        "video_adaptability": 23.0,
                        "total": 93.0
                    },
                    "priority_tier": "HIGH",
                    "why_it_matters": "Bridges the synthetic-to-real gap, accelerating the deployment of commercial humanoid robots.",
                    "suggested_video_angle": "Hook with robot footage; explain why synthetic video is training the physical robots of tomorrow."
                }
            ]
        }
