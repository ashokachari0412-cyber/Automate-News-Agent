"""Animated Talking Avatar Video Director — Vaibhav Sisinty Creator Style.

Key Directives:
1. Synthesizes a single consistent master avatar from assets/ reference photos.
2. Uses that single avatar throughout to generate real video where the avatar is TALKING and MOVING:
   - Synchronized audio-driven lip movements (mouth opens & articulates to voice amplitude).
   - Natural subtle head tilts, breathing motion, and periodic eye blinks.
   - 0-4s: Full-frame talking presenter seated behind podcast studio desk with mic + fast zoom punch-in on punchline.
   - Transitions: Oversized bold animated counters ("#01", "#02") with whoosh SFX.
   - Content: Full-screen terminal code / screencast B-roll + talking presenter in bottom-left rounded PiP frame.
   - Captions: High-energy kinetic bold captions with yellow punch-word highlights.
   - NO slides, NO markdown dashboards, NO text cards, NO bullet points.
"""

import asyncio
import logging
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config.settings import settings
from src.models.script import VideoBlueprint

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
VIDEO_OUTPUT_DIR = BASE_DIR / "artifacts" / "videos"
ASSETS_DIR = BASE_DIR / "assets"


class MasterAvatarEngine:
    """Creates and manages the single unified master avatar from reference images."""

    def __init__(self, assets_dir: Path = ASSETS_DIR):
        self.assets_dir = assets_dir
        self.master_avatar_path = self.assets_dir / "master_avatar.png"
        self._ensure_master_avatar()

    def _ensure_master_avatar(self):
        """Builds a single canonical master avatar image from assets photos."""
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        if self.master_avatar_path.exists():
            return

        # Find best frontal photo
        front_candidates = [
            self.assets_dir / "front.jpg",
            self.assets_dir / "Front_2.jpg",
            self.assets_dir / "avatar.png",
        ]
        source_path = None
        for p in front_candidates:
            if p.exists():
                source_path = p
                break

        if not source_path:
            # Fallback to any image
            for ext in (".jpg", ".jpeg", ".png"):
                found = list(self.assets_dir.glob(f"*{ext}"))
                if found:
                    source_path = found[0]
                    break

        if source_path and source_path.exists():
            img = Image.open(source_path).convert("RGBA")
        else:
            img = Image.new("RGBA", (512, 512), (15, 23, 42, 255))
            d = ImageDraw.Draw(img)
            d.ellipse([20, 20, 492, 492], fill=(30, 41, 59, 255), outline=(56, 189, 248, 255), width=6)
            d.ellipse([180, 100, 332, 252], fill=(226, 232, 240, 255))
            d.chord([100, 270, 412, 540], start=180, end=360, fill=(56, 189, 248, 255))

        target_w, target_h = 720, 1280
        scale = max(target_w / img.width, target_h / img.height)
        nw, nh = int(img.width * scale), int(img.height * scale)
        scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
        left = (nw - target_w) // 2
        top = max(0, int((nh - target_h) * 0.12))
        master = scaled.crop((left, top, left + target_w, top + target_h))
        master.save(self.master_avatar_path, "PNG")
        logger.info(f"Master avatar synthesized and saved to {self.master_avatar_path}")


class GeminiVeoAvatarEngine:
    """Uses Google Gemini Veo to synthesize real talking motion video of the user's clone."""

    def __init__(self, assets_dir: Path = ASSETS_DIR):
        self.assets_dir = assets_dir
        self.veo_clip_path = self.assets_dir / "veo_avatar_talking.mp4"
        self._ffmpeg_exe = self._resolve_ffmpeg()

    def _resolve_ffmpeg(self) -> str:
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return "ffmpeg"

    def find_existing_video_clip(self) -> Optional[Path]:
        """Checks for pre-rendered or user-supplied motion video clips."""
        candidates = [
            self.assets_dir / "clone_video.mp4",
            self.assets_dir / "clone_talking.mp4",
            self.assets_dir / "presenter.mp4",
            self.veo_clip_path,
        ]
        for p in candidates:
            if p.exists() and p.stat().st_size > 1000:
                return p
        return None

    def get_source_photo(self) -> Optional[Path]:
        front_candidates = [
            self.assets_dir / "front.jpg",
            self.assets_dir / "Front_2.jpg",
            self.assets_dir / "avatar.png",
        ]
        for p in front_candidates:
            if p.exists():
                return p
        for ext in (".jpg", ".jpeg", ".png"):
            found = list(self.assets_dir.glob(f"*{ext}"))
            if found:
                return found[0]
        return None

    async def get_or_generate_avatar_frames(
        self,
        fps: int = 20,
        prompt_topic: str = "AI Technology Updates",
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> List[Image.Image]:
        """Returns frames of the talking clone. Uses Gemini Veo if no clip exists yet."""
        # 1. Check if a motion video clip already exists
        existing_clip = self.find_existing_video_clip()
        if not existing_clip and getattr(settings, "ENABLE_GEMINI_VEO", True) and settings.GEMINI_API_KEY:
            if progress_callback:
                await self._notify(progress_callback, 20, "Synthesizing real AI video clone with Google Gemini Veo...")
            existing_clip = await self.generate_veo_clip(prompt_topic, progress_callback=progress_callback)

        # 2. Extract frames if clip is available
        if existing_clip and existing_clip.exists():
            frames = self._extract_frames_from_video(existing_clip, fps=fps)
            if frames:
                logger.info(f"Loaded {len(frames)} real video clone frames from {existing_clip}")
                return frames

        return []

    async def generate_veo_clip(
        self,
        prompt_topic: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Optional[Path]:
        """Calls Google Gemini Veo to generate a real motion video clone from the creator's photo."""
        photo_path = self.get_source_photo()
        if not photo_path:
            logger.warning("No reference photo found in assets/ for Gemini Veo.")
            return None

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            model_name = getattr(settings, "VEO_MODEL", "veo-3.1-fast-generate-preview")

            veo_prompt = (
                f"Cinematic portrait video of the presenter in the reference photo looking directly into camera, "
                f"actively talking and speaking with lively facial animation and natural articulated mouth movements, "
                f"blinking naturally, discussing {prompt_topic}, confident creator style in a modern dark-mode tech studio."
            )

            logger.info(f"Generating video clone with Google Gemini Veo model: {model_name}...")
            img_input = types.Image.from_file(location=str(photo_path))

            loop = asyncio.get_running_loop()
            operation = await loop.run_in_executor(
                None,
                lambda: client.models.generate_videos(
                    model=model_name,
                    image=img_input,
                    prompt=veo_prompt,
                    config=types.GenerateVideosConfig(
                        aspect_ratio="9:16",
                        duration_seconds=4,
                        person_generation="allow_adult"
                    )
                )
            )

            logger.info(f"Gemini Veo operation started: {operation.name}. Awaiting cloud render...")
            poll_interval = 6
            max_polls = 30

            for i in range(max_polls):
                await asyncio.sleep(poll_interval)
                if progress_callback:
                    await self._notify(
                        progress_callback,
                        22 + min(20, i * 2),
                        f"Gemini Veo rendering video clone ({i * poll_interval}s)..."
                    )

                updated_op = await loop.run_in_executor(
                    None,
                    lambda: client.operations.get(operation)
                )

                if updated_op.done:
                    if updated_op.error:
                        logger.error(f"Gemini Veo generation returned error: {updated_op.error}")
                        return None

                    if updated_op.response and updated_op.response.generated_videos:
                        first_video = updated_op.response.generated_videos[0].video
                        first_video.save(str(self.veo_clip_path))
                        logger.info(f"Gemini Veo clone video successfully saved to {self.veo_clip_path}")
                        return self.veo_clip_path
                    break

        except Exception as e:
            err_str = str(e)
            if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str:
                logger.warning(
                    f"Gemini Veo Quota Notice: Veo requires a linked Pay-As-You-Go billing project in Google AI Studio "
                    f"(https://aistudio.google.com/app/plan_and_billing). Falling back to master avatar. Error: {e}"
                )
            else:
                logger.error(f"Error during Gemini Veo generation: {e}")
        return None

    def _extract_frames_from_video(self, video_path: Path, fps: int = 20) -> List[Image.Image]:
        """Extracts scaled 720x1280 RGBA frames from video using FFmpeg."""
        frames = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_pattern = Path(temp_dir) / "frame_%04d.png"
            cmd = [
                self._ffmpeg_exe,
                "-i", str(video_path),
                "-vf", f"fps={fps},scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                str(temp_pattern)
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for p in sorted(Path(temp_dir).glob("frame_*.png")):
                try:
                    img = Image.open(p).convert("RGBA")
                    frames.append(img.copy())
                except Exception:
                    pass
        return frames

    async def _notify(self, callback: Callable, percent: int, msg: str):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(percent, msg)
            else:
                callback(percent, msg)
        except Exception:
            pass


class HeyGenAvatarEngine:
    """Generates authentic digital twin video clone with speech and motion via HeyGen API."""

    def __init__(self):
        self.api_key = getattr(settings, "HEYGEN_API_KEY", None)
        self.avatar_id = getattr(settings, "HEYGEN_AVATAR_ID", "0c8510c2f7ad4b96a5193bb7ce4c18a2")
        self.voice_id = getattr(settings, "HEYGEN_VOICE_ID", None)
        self.base_url = "https://api.heygen.com"

    async def generate_avatar_video(
        self,
        script_text: str,
        output_path: Path,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> bool:
        if not self.api_key:
            logger.warning("HEYGEN_API_KEY is not configured in .env.")
            return False

        headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

        # Keep script strictly short for 3-second testing (max ~7-8 words)
        words = script_text.strip().split()
        if len(words) > 8:
            script_text = " ".join(words[:7]) + "."

        if progress_callback:
            await self._notify(progress_callback, 20, "Submitting 3s digital clone request to HeyGen (Avatar: Ashok P)...")

        payload = {
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": self.avatar_id,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "text",
                        "input_text": script_text,
                    },
                    "background": {
                        "type": "color",
                        "value": "#0d1117",
                    },
                }
            ],
            "dimension": {
                "width": 720,
                "height": 1280,
            },
        }
        if self.voice_id:
            payload["video_inputs"][0]["voice"]["voice_id"] = self.voice_id

        try:
            import httpx
            async with httpx.AsyncClient(timeout=90.0) as client:
                # 1. Submit video generation request (Try v2 then v3)
                resp = await client.post(f"{self.base_url}/v2/video/generate", headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.info(f"HeyGen v2 returned status {resp.status_code}, trying v3 videos endpoint...")
                    v3_payload = {
                        "type": "avatar",
                        "avatar_id": self.avatar_id,
                        "script": script_text,
                        "dimension": {"width": 720, "height": 1280},
                    }
                    if self.voice_id:
                        v3_payload["voice_id"] = self.voice_id
                    resp = await client.post(
                        f"{self.base_url}/v3/videos",
                        headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                        json=v3_payload,
                    )

                resp_data = resp.json()
                logger.info(f"HeyGen generate response: {resp_data}")

                video_id = resp_data.get("data", {}).get("video_id") or resp_data.get("video_id")
                if not video_id:
                    err_msg = resp_data.get("message") or resp_data.get("error") or str(resp_data)
                    logger.error(f"HeyGen failed to initiate video: {err_msg}")
                    if progress_callback:
                        await self._notify(progress_callback, 25, f"HeyGen notice: {err_msg}")
                    return False

                if progress_callback:
                    await self._notify(progress_callback, 45, f"HeyGen queued video {video_id}. Rendering Ashok P clone...")

                # 2. Poll for rendering completion
                poll_interval = 4
                max_polls = 45  # ~3 mins
                video_url = None

                for i in range(max_polls):
                    await asyncio.sleep(poll_interval)
                    if progress_callback:
                        await self._notify(
                            progress_callback,
                            50 + min(35, i * 2),
                            f"HeyGen rendering your digital twin video ({i * poll_interval}s)...",
                        )

                    status_resp = await client.get(
                        f"{self.base_url}/v1/video_status.get",
                        headers=headers,
                        params={"video_id": video_id},
                    )
                    if status_resp.status_code != 200:
                        status_resp = await client.get(
                            f"{self.base_url}/v3/videos/{video_id}",
                            headers={"x-api-key": self.api_key},
                        )

                    sdata = status_resp.json()
                    data_obj = sdata.get("data", {}) or sdata
                    status = data_obj.get("status")

                    if status == "completed":
                        video_url = data_obj.get("video_url")
                        break
                    elif status == "failed":
                        logger.error(f"HeyGen video generation failed: {data_obj}")
                        return False

                if not video_url:
                    logger.error("HeyGen video generation timed out.")
                    return False

                # 3. Download the generated digital twin video
                if progress_callback:
                    await self._notify(progress_callback, 90, "Downloading HeyGen 3s talking clone video...")

                dl_resp = await client.get(video_url)
                if dl_resp.status_code == 200:
                    output_path.write_bytes(dl_resp.content)
                    logger.info(f"HeyGen digital twin video saved to {output_path} ({len(dl_resp.content)} bytes)")
                    return True

        except Exception as e:
            logger.error(f"HeyGen video generation error: {e}")
            return False

        return False

    async def _notify(self, callback: Callable, percent: int, msg: str):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(percent, msg)
            else:
                callback(percent, msg)
        except Exception:
            pass


def _get_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _split_into_kinetic_bursts(text: str, max_words: int = 3) -> List[str]:
    clean = re.sub(r"[^\w\s\$\-\.\,\!\?]", "", text).strip()
    words = clean.split()
    bursts = []
    i = 0
    while i < len(words):
        chunk = words[i:i + max_words]
        bursts.append(" ".join(chunk).upper())
        i += max_words
    return bursts or ["BREAKING AI UPDATE"]


class VideoGenerator:
    """Produces short-form video where a single avatar is genuinely talking and moving."""

    def __init__(self):
        VIDEO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._ffmpeg_exe = self._resolve_ffmpeg()
        self.avatar_engine = MasterAvatarEngine(ASSETS_DIR)
        self.veo_engine = GeminiVeoAvatarEngine(ASSETS_DIR)
        self.heygen_engine = HeyGenAvatarEngine()

    def _resolve_ffmpeg(self) -> str:
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return "ffmpeg"

    async def generate_video(
        self,
        blueprint: VideoBlueprint,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> str:
        output_filename = f"{blueprint.news_id}_{int(blueprint.estimated_duration_seconds)}s.mp4"
        output_path = VIDEO_OUTPUT_DIR / output_filename

        logger.info(f"Video Director: Rendering digital clone video for '{blueprint.title}'...")

        # -------------------------------------------------------------
        # 1. HeyGen AI Digital Twin Generator (Ashok P Clone)
        # -------------------------------------------------------------
        if getattr(settings, "HEYGEN_API_KEY", None):
            script_narration = blueprint.hook
            if blueprint.scenes and len(blueprint.scenes) > 0:
                script_narration = blueprint.scenes[0].spoken_script or blueprint.hook

            logger.info("HeyGen API Key detected: Synthesizing real talking clone of Ashok P...")
            heygen_success = await self.heygen_engine.generate_avatar_video(
                script_text=script_narration,
                output_path=output_path,
                progress_callback=progress_callback
            )
            if heygen_success and output_path.exists() and output_path.stat().st_size > 1000:
                logger.info(f"HeyGen video clone generated successfully: {output_path}")
                if progress_callback:
                    await self._notify(progress_callback, 100, "HeyGen digital clone ready! Ready.")
                return str(output_path)
            logger.warning("HeyGen generation failed or was bypassed. Falling back to local synthesizer...")

        if progress_callback:
            await self._notify(progress_callback, 15, "Synthesizing voiceover audio & speech energy envelope...")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            raw_voice_path = temp_path / "voiceover.mp3"
            final_audio_path = temp_path / "final_audio.mp3"

            # 1. Synthesize Audio via Edge-TTS
            audio_duration = await self._generate_voiceover(blueprint, str(raw_voice_path))
            total_duration = max(audio_duration, 8.0)

            # 2. Extract Speech Energy Envelope for Lip-Sync
            fps = 20
            envelope = self._extract_audio_envelope(str(raw_voice_path), fps=fps)

            # 3. Retrieve or synthesize Gemini Veo avatar motion frames
            veo_frames = await self.veo_engine.get_or_generate_avatar_frames(
                fps=fps,
                prompt_topic=blueprint.title,
                progress_callback=progress_callback
            )

            # 4. Mix Whoosh SFX at transitions
            transition_times = [3.8, 8.2]
            if progress_callback:
                await self._notify(progress_callback, 40, "Mixing dynamic whoosh audio transitions...")

            self._mix_audio_with_whooshes(
                voice_path=str(raw_voice_path),
                out_audio_path=str(final_audio_path),
                transition_times=transition_times,
                total_duration=total_duration
            )

            # 5. Stream Talking & Moving Avatar Frames directly into FFmpeg
            if progress_callback:
                await self._notify(progress_callback, 65, "Synthesizing animated talking avatar & kinetic captions...")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._render_talking_avatar_video,
                blueprint,
                envelope,
                str(final_audio_path) if final_audio_path.exists() else str(raw_voice_path),
                str(output_path),
                total_duration,
                fps,
                veo_frames,
            )

            if progress_callback:
                await self._notify(progress_callback, 100, "Talking avatar video ready! Ready.")

        logger.info(f"Video Director: Output saved to {output_path}")
        return str(output_path)

    async def _notify(self, callback: Callable, percent: int, msg: str):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(percent, msg)
            else:
                callback(percent, msg)
        except Exception:
            pass

    async def _generate_voiceover(self, blueprint: VideoBlueprint, audio_output_path: str) -> float:
        spoken_parts = []
        if blueprint.hook:
            spoken_parts.append(blueprint.hook)
        for s in (blueprint.scenes or []):
            text = getattr(s, "spoken_script", "")
            if text and text not in spoken_parts:
                spoken_parts.append(text)

        full_narration = " ".join(spoken_parts)
        if not full_narration.strip():
            full_narration = f"OpenAI just launched Operator. {blueprint.title}. Follow for more AI updates."

        voice = getattr(settings, "TTS_VOICE", "en-US-AndrewNeural")
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text=full_narration, voice=voice)
            await communicate.save(audio_output_path)
            duration = self._get_audio_duration(audio_output_path)
            return duration
        except Exception as e:
            logger.warning(f"Voiceover error: {e}")
            return 12.0

    def _get_audio_duration(self, audio_file: str) -> float:
        try:
            cmd = [self._ffmpeg_exe, "-i", audio_file]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="ignore")
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
        except Exception:
            pass
        return 12.0

    def _extract_audio_envelope(self, audio_file: str, fps: int = 20) -> List[float]:
        """Extracts normalized speech volume envelope (0.0 to 1.0) for every video frame."""
        if not os.path.exists(audio_file):
            return [0.0] * (fps * 15)

        try:
            cmd = [self._ffmpeg_exe, "-i", audio_file, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                return [0.5] * (fps * 15)

            pcm = np.frombuffer(res.stdout, dtype=np.int16)
            samples_per_frame = 16000 // fps
            total_frames = len(pcm) // samples_per_frame
            envelope = []

            for i in range(total_frames):
                chunk = pcm[i * samples_per_frame:(i + 1) * samples_per_frame]
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                # Normalize speech envelope
                norm = min(1.0, max(0.0, (rms - 350.0) / 3200.0))
                envelope.append(norm)
            return envelope or [0.5] * (fps * 15)
        except Exception as e:
            logger.debug(f"Envelope extraction error: {e}")
            return [0.5] * (fps * 15)

    def _mix_audio_with_whooshes(
        self,
        voice_path: str,
        out_audio_path: str,
        transition_times: List[float],
        total_duration: float
    ):
        """Overlays whoosh transition sound effects onto the voiceover."""
        if not os.path.exists(voice_path):
            return

        if not transition_times:
            import shutil
            shutil.copy(voice_path, out_audio_path)
            return

        try:
            filter_parts = []
            inputs = ["-i", voice_path]

            for idx, t in enumerate(transition_times, start=1):
                delay_ms = int(t * 1000)
                inputs.extend(["-f", "lavfi", "-i", "anoisesrc=d=0.35:c=pink:r=44100,afade=t=in:ss=0:d=0.04,afade=t=out:st=0.18:d=0.17,volume=0.35"])
                filter_parts.append(f"[{idx}]adelay={delay_ms}|{delay_ms}[sfx{idx}];")

            mix_ins = "".join([f"[sfx{idx}]" for idx in range(1, len(transition_times) + 1)])
            filter_complex = "".join(filter_parts) + f"[0]{mix_ins}amix=inputs={len(transition_times)+1}:duration=first[outa]"

            cmd = [self._ffmpeg_exe, "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[outa]", "-c:a", "libmp3lame", out_audio_path]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                import shutil
                shutil.copy(voice_path, out_audio_path)
        except Exception:
            import shutil
            shutil.copy(voice_path, out_audio_path)

    def _render_talking_avatar_video(
        self,
        blueprint: VideoBlueprint,
        envelope: List[float],
        audio_path: str,
        output_path: str,
        total_duration: float,
        fps: int = 20,
        veo_frames: Optional[List[Image.Image]] = None,
    ):
        """Pipes real-time synthesized talking and moving avatar frames into FFmpeg."""
        width, height = 720, 1280
        total_frames = int(total_duration * fps)

        # Base fallback image
        master_img = Image.open(self.avatar_engine.master_avatar_path).convert("RGBA")
        is_live_video = bool(veo_frames and len(veo_frames) > 0)

        # Prepare kinetic captions timeline
        all_text = f"{blueprint.hook} "
        for s in (blueprint.scenes or []):
            all_text += f"{getattr(s, 'spoken_script', '')} "
        caption_bursts = _split_into_kinetic_bursts(all_text, max_words=3)

        # Launch FFmpeg stdin pipe
        cmd = [
            self._ffmpeg_exe,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{width}x{height}",
            "-pix_fmt", "rgba",
            "-r", str(fps),
            "-i", "-",
            "-i", audio_path,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]

        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        try:
            for frame_idx in range(total_frames):
                t = frame_idx / float(fps)
                energy = envelope[frame_idx] if frame_idx < len(envelope) else 0.0

                current_avatar = veo_frames[frame_idx % len(veo_frames)] if is_live_video else master_img

                # Determine active scene & caption
                frame_img = self._synthesize_frame(
                    master_img=current_avatar,
                    t=t,
                    energy=energy,
                    total_duration=total_duration,
                    caption_bursts=caption_bursts,
                    topic=blueprint.title,
                    width=width,
                    height=height,
                    is_live_video=is_live_video,
                )

                proc.stdin.write(frame_img.tobytes())

            proc.stdin.close()
            proc.wait()
            logger.info("FFmpeg video rendering pipe completed successfully.")
        except Exception as e:
            logger.error(f"Error during talking avatar streaming: {e}")
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.kill()

    def _synthesize_frame(
        self,
        master_img: Image.Image,
        t: float,
        energy: float,
        total_duration: float,
        caption_bursts: List[str],
        topic: str,
        width: int,
        height: int,
        is_live_video: bool = False,
    ) -> Image.Image:
        """Synthesizes a single 9:16 frame with talking avatar, head movement, transitions & captions."""
        # Timeline Breakdown:
        # 0.0 - 1.8s: Intro Full-Frame Presenter (zoom 1.0x)
        # 1.8 - 3.8s: Fast Zoom Punch-in on Presenter (zoom 1.24x)
        # 3.8 - 4.5s: Transition Flash "#01"
        # 4.5 - 8.2s: Demo Terminal Screencast B-Roll + Bottom-Left Talking PiP
        # 8.2 - 8.9s: Transition Flash "#02"
        # 8.9 - 12.5s: Demo UI / Benchmark Screencast B-Roll + Bottom-Left Talking PiP
        # 12.5s - End: Full-Frame Presenter Outro Punch-In

        if 3.8 <= t < 4.5:
            # Transition #01
            return self._draw_transition_frame("#01", "BREAKTHROUGH #01", width, height)
        elif 8.2 <= t < 8.9:
            # Transition #02
            return self._draw_transition_frame("#02", "CORE IMPACT #02", width, height)

        # Active Caption Index based on time
        cap_idx = min(int((t / max(total_duration, 1.0)) * len(caption_bursts)), len(caption_bursts) - 1)
        active_caption = caption_bursts[cap_idx] if caption_bursts else "AI BREAKTHROUGH"

        if t < 3.8 or t >= 12.5:
            # Full-Frame Talking Presenter behind Podcast Desk
            zoom = 1.24 if (1.8 <= t < 3.8 or t >= 12.5) else 1.0
            return self._render_full_talking_presenter(
                master_img=master_img,
                t=t,
                energy=energy,
                zoom=zoom,
                caption=active_caption,
                width=width,
                height=height,
                is_live_video=is_live_video,
            )
        else:
            # Screencast B-Roll + Talking Presenter in Bottom-Left PiP
            screencast_style = "terminal" if t < 8.2 else "browser_ui"
            return self._render_demo_broll_with_pip(
                master_img=master_img,
                t=t,
                energy=energy,
                style=screencast_style,
                caption=active_caption,
                topic=topic,
                width=width,
                height=height,
                is_live_video=is_live_video,
            )

    def _render_full_talking_presenter(
        self,
        master_img: Image.Image,
        t: float,
        energy: float,
        zoom: float,
        caption: str,
        width: int,
        height: int,
        is_live_video: bool = False,
    ) -> Image.Image:
        """Full-frame presenter with animated talking mouth, natural head tilt, and studio podcast mic."""
        # 1. Natural Head Motion & Posture Float
        if is_live_video:
            talking_avatar = master_img.copy()
            head_angle = 0.0
        else:
            head_angle = math.sin(t * 1.6) * 1.3  # Subtle talking head sway
            talking_avatar = self._animate_talking_avatar(master_img, energy=energy, t=t)

        # 2. Apply Camera Zoom & Centering
        if zoom > 1.01:
            zw, zh = int(width / zoom), int(height / zoom)
            cx, cy = width // 2, int(height * 0.40)
            zl = max(0, min(width - zw, cx - zw // 2))
            zt = max(0, min(height - zh, cy - zh // 2))
            frame = talking_avatar.crop((zl, zt, zl + zw, zt + zh)).resize((width, height), Image.Resampling.LANCZOS)
        else:
            frame = talking_avatar.copy()

        # Apply head tilt if static image
        if abs(head_angle) > 0.1:
            frame = frame.rotate(head_angle, resample=Image.Resampling.BILINEAR, center=(width // 2, int(height * 0.40)))

        # 3. Foreground Podcast Desk Microphone Overlay (Shure SM7B studio look)
        self._draw_podcast_microphone(frame, width, height)

        # 4. Dynamic Kinetic Caption in Lower Third
        self._draw_kinetic_caption(frame, caption, width, y_pos=int(height * 0.72))
        return frame

    def _render_demo_broll_with_pip(
        self,
        master_img: Image.Image,
        t: float,
        energy: float,
        style: str,
        caption: str,
        topic: str,
        width: int,
        height: int,
        is_live_video: bool = False,
    ) -> Image.Image:
        """Full-screen code screencast B-roll with talking master avatar in bottom-left PiP."""
        # 1. Create Screencast B-Roll background
        frame = Image.new("RGBA", (width, height), (13, 17, 23, 255))
        draw = ImageDraw.Draw(frame)

        # Mac window title bar
        draw.rectangle([0, 0, width, 55], fill=(22, 27, 34, 255))
        draw.ellipse([20, 20, 34, 34], fill=(255, 95, 86, 255))
        draw.ellipse([44, 20, 58, 34], fill=(255, 189, 46, 255))
        draw.ellipse([68, 20, 82, 34], fill=(39, 201, 63, 255))

        f_bar = _get_font(18, bold=True)
        draw.text((110, 18), "operator-agent-runtime • zsh", fill=(148, 163, 184), font=f_bar)

        f_code = _get_font(21, bold=False)
        if style == "terminal":
            code_lines = [
                ("$ python -m agent.runner --task 'autonomous-web'", (56, 189, 248)),
                ("[INIT] Initializing multi-step browser execution...", (148, 163, 184)),
                ("[BROWSER] Chromium headless daemon attached", (34, 197, 94)),
                ("[AUTH] Token injection & verified credentials", (250, 204, 21)),
                ("[NAV] -> https://app.enterprise.io/workflows", (148, 163, 184)),
                ("[ACTION] Clicking element: button#execute-all", (244, 114, 182)),
                ("[DOM] Parsing shadow root & dynamic tables...", (148, 163, 184)),
                ("[DATA] Extracted 42 enterprise records in 1.4s", (34, 197, 94)),
                ("[STATUS] 200 OK — Autonomous task verified!", (56, 189, 248)),
                ("$ _", (255, 255, 255)),
            ]
        else:
            code_lines = [
                ("MODEL: Gemini 2.5 Flash Thinking (Test-Time Compute)", (250, 204, 21)),
                ("CONTEXT: 1,000,000 Tokens (Native Multimodal)", (56, 189, 248)),
                ("BENCHMARK: SWE-Bench Verified -> 68.4%", (34, 197, 94)),
                ("LATENCY: 3.2x faster inference on coding trees", (148, 163, 184)),
                ("SEARCH DEPTH: 16 branches / Monte Carlo Search", (244, 114, 182)),
                ("STATUS: Production API Ready for Developers", (34, 197, 94)),
                ("IMPACT: Replaces prompt trial-and-error completely", (241, 245, 249)),
                ("ENDPOINT: https://ai.google.dev/v1/models/stream", (56, 189, 248)),
            ]

        for idx, (line, color) in enumerate(code_lines):
            draw.text((35, 90 + (idx * 48)), line, fill=color, font=f_code)

        # 2. Kinetic Captions in Center-Right
        self._draw_kinetic_caption(frame, caption, width, y_pos=int(height * 0.62))

        # ================================================================
        # OVERLAY (PiP): Talking & moving master avatar in bottom-left
        # ================================================================
        pip_w, pip_h = 240, 310
        pip_x, pip_y = 35, height - pip_h - 45

        # Animate or use live avatar for PiP
        if is_live_video:
            talking_pip_source = master_img.copy()
            pip_angle = 0.0
        else:
            talking_pip_source = self._animate_talking_avatar(master_img, energy=energy, t=t)
            pip_angle = math.sin(t * 1.8) * 1.5

        # Crop headshot for PiP
        cx, cy = width // 2, int(height * 0.40)
        pip_crop = talking_pip_source.crop((cx - 180, cy - 200, cx + 180, cy + 260)).resize((pip_w, pip_h), Image.Resampling.LANCZOS)

        if abs(pip_angle) > 0.1:
            pip_crop = pip_crop.rotate(pip_angle, resample=Image.Resampling.BILINEAR)

        # Rounded rectangle mask
        mask = Image.new("L", (pip_w, pip_h), 0)
        m_draw = ImageDraw.Draw(mask)
        m_draw.rounded_rectangle([0, 0, pip_w, pip_h], radius=22, fill=255)

        pip_out = Image.new("RGBA", (pip_w, pip_h), (0, 0, 0, 0))
        pip_out.paste(pip_crop, (0, 0), mask=mask)

        # Cyber neon glowing border
        b_draw = ImageDraw.Draw(pip_out)
        b_draw.rounded_rectangle([2, 2, pip_w - 2, pip_h - 2], radius=22, outline=(56, 189, 248, 255), width=4)

        frame.paste(pip_out, (pip_x, pip_y), mask=pip_out)

        # Host badge
        badge_w, badge_h = 140, 34
        bx, by = pip_x + (pip_w - badge_w) // 2, pip_y + pip_h - 18
        draw.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=10, fill=(15, 23, 42, 255), outline=(56, 189, 248, 255), width=2)
        f_badge = _get_font(16, bold=True)
        draw.text((bx + 18, by + 6), f"🎙️ {settings.USER_NAME}", fill=(241, 245, 249), font=f_badge)

        return frame

    def _animate_talking_avatar(self, base_avatar: Image.Image, energy: float, t: float) -> Image.Image:
        """Deforms avatar mouth and eyes dynamically based on speech energy (Lip-Sync)."""
        w, h = base_avatar.size
        frame = base_avatar.copy()

        # Face center estimates
        mx, my = int(w * 0.50), int(h * 0.44)
        mw, mh = int(w * 0.16), int(h * 0.05)

        # 1. Talking Mouth Animation
        if energy > 0.12:
            # Jaw drop proportional to speech volume (2px to 8px)
            drop_px = int(energy * 8)
            lower_lip_box = (mx - mw // 2, my, mx + mw // 2, my + mh + 22)
            lower_slice = base_avatar.crop(lower_lip_box)

            # Dark inner mouth opening
            draw = ImageDraw.Draw(frame)
            draw.ellipse([mx - 24, my - 2, mx + 24, my + drop_px + 5], fill=(45, 18, 22, 255))
            # Subtle teeth highlight
            draw.rectangle([mx - 15, my, mx + 15, my + 3], fill=(235, 235, 240, 210))

            # Paste lower lip slice down
            frame.paste(lower_slice, (lower_lip_box[0], lower_lip_box[1] + drop_px))

        # 2. Natural Eye Blinking (Brief 0.12s blink every ~3.6 seconds)
        blink_cycle = t % 3.6
        if 0.0 <= blink_cycle < 0.12:
            draw = ImageDraw.Draw(frame)
            ey = int(h * 0.32)
            # Left eye blink
            draw.rectangle([int(w * 0.40) - 18, ey, int(w * 0.40) + 18, ey + 7], fill=(160, 110, 85, 255))
            draw.line([(int(w * 0.40) - 18, ey + 4), (int(w * 0.40) + 18, ey + 4)], fill=(40, 25, 20, 255), width=2)
            # Right eye blink
            draw.rectangle([int(w * 0.60) - 18, ey, int(w * 0.60) + 18, ey + 7], fill=(160, 110, 85, 255))
            draw.line([(int(w * 0.60) - 18, ey + 4), (int(w * 0.60) + 18, ey + 4)], fill=(40, 25, 20, 255), width=2)

        return frame

    def _draw_podcast_microphone(self, base_img: Image.Image, width: int, height: int):
        """Draws realistic podcast studio microphone in the foreground desk."""
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        mx, my = width - 180, height - 200

        # Metal boom arm
        draw.line([(mx + 40, height), (mx + 20, my + 70)], fill=(40, 45, 55, 255), width=14)
        draw.line([(mx + 20, my + 70), (mx - 15, my + 25)], fill=(30, 35, 45, 255), width=12)

        # Shockmount ring
        draw.ellipse([mx - 55, my - 15, mx + 25, my + 65], outline=(71, 85, 105, 255), width=5)

        # Microphone body & foam windscreen (dark matte capsule)
        draw.rounded_rectangle([mx - 48, my - 45, mx + 18, my + 45], radius=16, fill=(24, 28, 36, 255))
        # Silver acoustic ring
        draw.line([(mx - 48, my), (mx + 18, my)], fill=(148, 163, 184, 255), width=3)
        # Foam texture capsule top
        draw.ellipse([mx - 45, my - 65, mx + 15, my - 25], fill=(30, 35, 45, 255))

        base_img.paste(overlay, (0, 0), mask=overlay)

    def _draw_transition_frame(self, counter: str, tag: str, width: int, height: int) -> Image.Image:
        """Draws the oversized bold counter transition ('#01', '#02') with neon styling."""
        img = Image.new("RGBA", (width, height), (10, 15, 26, 255))
        draw = ImageDraw.Draw(img)

        cx, cy = width // 2, height // 2

        # Glowing backdrop
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        g_draw = ImageDraw.Draw(glow)
        g_draw.ellipse([cx - 240, cy - 200, cx + 240, cy + 200], fill=(250, 204, 21, 60))
        g_draw.ellipse([cx - 160, cy - 120, cx + 160, cy + 120], fill=(56, 189, 248, 80))
        glow = glow.filter(ImageFilter.GaussianBlur(40))
        img.paste(glow, (0, 0), mask=glow)

        # Brand / Tag pill
        f_tag = _get_font(24, bold=True)
        tw = int(draw.textlength(tag, font=f_tag)) if hasattr(draw, "textlength") else 240
        draw.rounded_rectangle([cx - tw // 2 - 25, cy - 180, cx + tw // 2 + 25, cy - 130], radius=15, fill=(30, 41, 59, 255), outline=(56, 189, 248, 255), width=2)
        draw.text((cx - tw // 2, cy - 170), tag, fill=(56, 189, 248), font=f_tag)

        # Huge Oversized Counter: "#01" / "#02"
        f_num = _get_font(148, bold=True)
        nw = int(draw.textlength(counter, font=f_num)) if hasattr(draw, "textlength") else 320
        draw.text((cx - nw // 2 + 6, cy - 80 + 6), counter, fill=(0, 0, 0), font=f_num)
        draw.text((cx - nw // 2, cy - 80), counter, fill=(250, 204, 21), font=f_num, stroke_width=6, stroke_fill=(15, 23, 42))

        f_sub = _get_font(26, bold=True)
        sub_text = "NEXT-GEN AI WORKFLOW"
        sw = int(draw.textlength(sub_text, font=f_sub)) if hasattr(draw, "textlength") else 300
        draw.text((cx - sw // 2, cy + 120), sub_text, fill=(241, 245, 249), font=f_sub)

        return img

    def _draw_kinetic_caption(self, base_img: Image.Image, text: str, width: int, y_pos: int):
        """Renders viral kinetic captions with bold sans-serif styling and yellow highlight."""
        draw = ImageDraw.Draw(base_img)
        words = text.split()
        if not words:
            return

        f_cap = _get_font(46, bold=True)
        word_widths = []
        for w in words:
            w_len = int(draw.textlength(w, font=f_cap)) if hasattr(draw, "textlength") else len(w) * 24
            word_widths.append(w_len)

        space_w = 16
        total_w = sum(word_widths) + (space_w * (len(words) - 1))
        start_x = max(20, (width - total_w) // 2)

        # Translucent dark pill backdrop for 100% legibility
        bg_pad = 16
        pill_box = [start_x - bg_pad, y_pos - 8, start_x + total_w + bg_pad, y_pos + 62]
        bg_pill = Image.new("RGBA", (width, base_img.height), (0, 0, 0, 0))
        bg_draw = ImageDraw.Draw(bg_pill)
        bg_draw.rounded_rectangle(pill_box, radius=16, fill=(0, 0, 0, 160))
        base_img.paste(bg_pill, (0, 0), mask=bg_pill)

        # Highlight first or longest word
        punch_idx = 0
        cur_x = start_x
        for i, word in enumerate(words):
            is_punch = (i == punch_idx)
            text_color = (250, 204, 21) if is_punch else (255, 255, 255)
            draw.text(
                (cur_x, y_pos),
                word,
                fill=text_color,
                font=f_cap,
                stroke_width=5,
                stroke_fill=(0, 0, 0)
            )
            cur_x += word_widths[i] + space_w
