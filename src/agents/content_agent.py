"""Content Agent: Generates 35-40s Video Blueprint, Hook & Scene Clips (Box 6 in Architecture)."""

import json
import logging
from typing import Optional
from config.settings import settings
from src.llm.client import LLMClient
from src.models.news import PrioritizedNewsItem
from src.models.script import SceneClip, VideoBlueprint

logger = logging.getLogger(__name__)

CONTENT_AGENT_SYSTEM_PROMPT = """You are an elite short-form video director and viral scriptwriter for AI founders.
Your job is to transform breaking AI news into an ultra-engaging 35-40 second video script suitable for Instagram Reels, YouTube Shorts, and TikTok.

Rules for the 35-40s Video:
1. Hook (0-5s): Needs to be an unexpected, high-retention statement that stops the scroll immediately.
2. Scene 1 (0-10s): Avatar introduces the breakthrough with punchy energy and on-screen caption.
3. Scene 2 (10-24s): Breakdown of the core technology, benchmark metric, or live demo (B-roll footage/screen capture).
4. Scene 3 (24-34s): Why this matters to developers and the world.
5. Scene 4 (34-38s): Strong punchline/question + quick call-to-action.
6. Target total duration: Exactly 35 to 40 seconds.
7. Return strictly valid JSON.
"""

SCRIPT_GENERATION_PROMPT = """Generate a 35-40 second short-form video blueprint for this breaking AI news story:

Story Title: {title}
Summary: {summary}
Key Takeaways: {key_points}
Category: {category}
Why It Matters: {why_it_matters}
Avatar Profile: {avatar_profile}

Return JSON with this exact schema:
{{
  "hook": "0-5s scroll-stopping opening line",
  "estimated_duration_seconds": 38,
  "scenes": [
    {{
      "scene_number": 1,
      "timestamp": "00:00 - 00:08",
      "duration_seconds": 8.0,
      "spoken_script": "Exact voiceover line spoken by avatar...",
      "visual_description": "Avatar in modern tech studio, large animated headline appears...",
      "on_screen_text": "BOLD 3-WORD POPUP"
    }},
    {{
      "scene_number": 2,
      "timestamp": "00:08 - 00:20",
      "duration_seconds": 12.0,
      "spoken_script": "Breakdown line with key numbers and facts...",
      "visual_description": "Cut to full-screen demo UI / benchmark chart showing 3x speedup...",
      "on_screen_text": "BENCHMARK SHIFT"
    }},
    {{
      "scene_number": 3,
      "timestamp": "00:20 - 00:32",
      "duration_seconds": 12.0,
      "spoken_script": "Explanation of practical developer and industry impact...",
      "visual_description": "Split screen avatar pointing to terminal executing agent workflow...",
      "on_screen_text": "WHY IT MATTERS"
    }},
    {{
      "scene_number": 4,
      "timestamp": "00:32 - 00:38",
      "duration_seconds": 6.0,
      "spoken_script": "Concluding punchline and call to action...",
      "visual_description": "Avatar close-up with follow button & topic title...",
      "on_screen_text": "ARE YOU READY?"
    }}
  ]
}}
"""


class ContentAgent:
    """Agent responsible for crafting viral 35-40s video scripts and clip breakdowns."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient(settings)

    async def generate_blueprint(
        self,
        news_item: PrioritizedNewsItem,
        avatar_profile: str = "Tech Founder (Professional / AI Pioneer)",
    ) -> VideoBlueprint:
        """Generate a complete 35-40s Video Blueprint using Google Gemini."""
        prompt = SCRIPT_GENERATION_PROMPT.format(
            title=news_item.title,
            summary=news_item.summary,
            key_points=" | ".join(news_item.key_bullet_points),
            category=news_item.category,
            why_it_matters=news_item.why_it_matters,
            avatar_profile=avatar_profile,
        )

        logger.info(f"Content Agent: Generating 35-40s video blueprint for '{news_item.title}' with Gemini...")

        resp = await self.llm.generate_json(prompt, system_prompt=CONTENT_AGENT_SYSTEM_PROMPT)

        scenes_data = resp.get("scenes", [])
        scenes: list[SceneClip] = []

        for idx, sc in enumerate(scenes_data, start=1):
            scenes.append(
                SceneClip(
                    scene_number=sc.get("scene_number", idx),
                    timestamp=sc.get("timestamp", f"00:{(idx-1)*10:02d} - 00:{idx*10:02d}"),
                    duration_seconds=float(sc.get("duration_seconds", 9.5)),
                    spoken_script=sc.get("spoken_script", ""),
                    visual_description=sc.get("visual_description", ""),
                    on_screen_text=sc.get("on_screen_text", ""),
                )
            )

        # Fallback default 4 scenes if LLM response did not include scenes
        if not scenes:
            hook_text = resp.get("hook") or news_item.hook or f"Breaking AI update: {news_item.title}."
            scenes = [
                SceneClip(
                    scene_number=1,
                    timestamp="00:00 - 00:08",
                    duration_seconds=8.0,
                    spoken_script=hook_text,
                    visual_description=f"Avatar in modern high-tech studio introducing {news_item.title}",
                    on_screen_text=news_item.category.upper(),
                ),
                SceneClip(
                    scene_number=2,
                    timestamp="00:08 - 00:20",
                    duration_seconds=12.0,
                    spoken_script=news_item.summary,
                    visual_description="B-roll screen demonstration showing live benchmark and capabilities",
                    on_screen_text="THE BREAKTHROUGH",
                ),
                SceneClip(
                    scene_number=3,
                    timestamp="00:20 - 00:32",
                    duration_seconds=12.0,
                    spoken_script=f"Why this matters: {news_item.why_it_matters}",
                    visual_description="Split screen avatar showing developer workflow and terminal execution",
                    on_screen_text="WHY IT MATTERS",
                ),
                SceneClip(
                    scene_number=4,
                    timestamp="00:32 - 00:38",
                    duration_seconds=6.0,
                    spoken_script="Follow for the latest daily AI intelligence and agent architectures.",
                    visual_description="Avatar close-up with animated follow and notification badge",
                    on_screen_text="FOLLOW FOR AI NEWS",
                ),
            ]

        duration = int(settings.VIDEO_DURATION_SECONDS or 3)
        hook = resp.get("hook", news_item.hook or "Check out this AI breakthrough.")

        if duration <= 3:
            # 3-second rapid test: Keep script strictly to 1 punchy short sentence (approx 7 words)
            short_script = hook
            words = short_script.split()
            if len(words) > 8:
                short_script = " ".join(words[:7]) + "."
            scenes = [
                SceneClip(
                    scene_number=1,
                    timestamp="00:00 - 00:03",
                    duration_seconds=3.0,
                    spoken_script=short_script,
                    visual_description=f"Ashok P avatar speaking directly to camera presenting {news_item.title}",
                    on_screen_text=news_item.title[:30].upper(),
                )
            ]
            hook = short_script

        first_url = news_item.source_urls[0] if news_item.source_urls else ""

        blueprint = VideoBlueprint(
            news_id=news_item.id,
            title=news_item.title,
            hook=hook,
            estimated_duration_seconds=duration,
            scenes=scenes,
            avatar_profile=avatar_profile,
            aspect_ratio="9:16",
            primary_source_handle=news_item.primary_source_handle,
            source_url=first_url,
        )

        return blueprint
