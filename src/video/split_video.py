"""Split-screen 9:16 composer for Alfred Studio.

Final frame (1080x1920):
  top 65%     : content video (Wan 2.6 Flash / Omni Flash image-to-video, or a free FFmpeg zoom/pan)
  bottom 35%  : his HeyGen avatar clone (one continuous take; his voice is the video's audio)
  split line  : the two parts overlap by BLEND px and cross-fade, so there is no hard edge
  subtitles   : short 2-4 word captions just above the clone (white bold, black outline, key word in yellow)
  edges       : a soft fade to deep black at the very top and bottom
"""

import base64
import io
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 25
BLEND = 140            # px of cross-fade around the split line
EDGE_FADE = 70         # px fade to black at the very top / bottom
BOTTOM_RATIO = 0.35    # his clone takes the bottom 35% of the frame
PART_H = 1040          # working height for free zoom/pan clips (re-scaled by compose)

FONT_PATHS = ["C:/Windows/Fonts/seguibl.ttf", "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf"]
CAPTION_SIZE = 64
CAPTION_TEXT = (255, 255, 255, 255)
CAPTION_ACCENT = (255, 200, 61, 255)     # warm yellow: the key word of each caption
CAPTION_STROKE = 6                       # black outline around each letter (no background box)
STOPWORDS = {"a", "an", "the", "this", "that", "these", "those", "is", "are", "was", "were", "be", "to", "of",
             "and", "or", "for", "in", "on", "at", "it", "its", "i", "i'm", "you", "your", "we", "my", "me",
             "with", "so", "but", "can", "will", "just", "here", "what", "how", "now", "hi"}


def _layout(bottom_ratio: float = BOTTOM_RATIO) -> Tuple[int, int, int]:
    """(split line y, top part height, bottom part height); the parts overlap by BLEND around the split."""
    split = round(H * (1 - bottom_ratio))
    return split, split + BLEND // 2, H - split + BLEND // 2


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _run(args: List[str]) -> None:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {proc.stderr.strip()[-600:]}")


def media_duration(path: Path) -> float:
    """Duration in seconds, read from FFmpeg's banner (no ffprobe needed)."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    for line in proc.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"Could not read the duration of {path.name}")


def _mask(path: Path, height: int, fade_bottom: bool) -> Path:
    """Grayscale alpha mask: soft fade-in over the first EDGE_FADE px (the frame edge, deep black),
    and (for the top part) a fade-out over the last BLEND px into the clone below."""
    col = Image.new("L", (1, height), 255)
    px = col.load()
    for y in range(height):
        a = 255
        if y < EDGE_FADE:
            a = int(255 * y / EDGE_FADE)
        if fade_bottom and y >= height - BLEND:
            a = min(a, int(255 * (height - y) / BLEND))
        px[0, y] = a
    col.resize((W, height)).save(path)
    return path


# ----------------------------------------------------------------------------- top-half engines
def kenburns_clip(image: Path, seconds: float, out: Path, index: int = 0) -> Path:
    """Free fallback: a slow zoom (alternating in/out per clip) over the scene image."""
    frames = max(1, int(round(seconds * FPS)))
    zoom = f"1+0.10*on/{frames}" if index % 2 == 0 else f"1.10-0.10*on/{frames}"
    _run(["-loop", "1", "-t", f"{seconds:.3f}", "-i", str(image),
          "-vf", (f"scale={W * 2}:-2,zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d=1:s={W}x{PART_H}:fps={FPS},format=yuv420p"),
          "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    return out


def _dashscope_env() -> dict:
    """Alibaba Cloud Model Studio settings, read fresh from .env (no restart needed)."""
    values: dict = {}
    try:
        from dotenv import dotenv_values
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        values = {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(env_file).items()}
    except Exception:
        pass
    workspace = values.get("DASHSCOPE_WORKSPACE_ID", "")
    base = values.get("DASHSCOPE_BASE_URL") or (
        f"https://{workspace}.ap-southeast-1.maas.aliyuncs.com" if workspace else "https://dashscope-intl.aliyuncs.com")
    wavespeed_key = values.get("WAVESPEED_API_KEY", "")
    # WaveSpeed by default (Alibaba Cloud International doesn't accept India accounts)
    provider = (values.get("WAN_PROVIDER")
                or ("alibaba" if values.get("DASHSCOPE_API_KEY") and not wavespeed_key else "wavespeed")).lower()
    return {"provider": provider, "key": values.get("DASHSCOPE_API_KEY", ""), "base": base.rstrip("/"),
            "model": values.get("WAN_MODEL") or "wan2.6-i2v-flash", "wavespeed_key": wavespeed_key}


def _as_data_uri(image: Path, max_side: int = 1280) -> str:
    """JPEG data URI (Wan rejects PNGs with alpha; smaller upload)."""
    img = Image.open(image).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


WAVESPEED = "https://api.wavespeed.ai/api/v3"
NEGATIVE = "text, captions, watermark, logo, distorted faces, extra fingers, flicker"


def wan_clip(image: Path, prompt: str, seconds: float, out: Path, resolution: str = "720p") -> Path:
    """Wan 2.6 Flash image-to-video, SILENT (cheapest tier; his voice comes from HeyGen).
    Provider: WaveSpeed AI (works from India) or Alibaba Cloud Model Studio (WAN_PROVIDER)."""
    if _dashscope_env()["provider"] == "wavespeed":
        return _wan_wavespeed(image, prompt, seconds, out, resolution)
    return _wan_alibaba(image, prompt, seconds, out, resolution)


def _wan_wavespeed(image: Path, prompt: str, seconds: float, out: Path, resolution: str = "720p") -> Path:
    import httpx
    key = _dashscope_env()["wavespeed_key"]
    if not key:
        raise RuntimeError("WAVESPEED_API_KEY is not set in .env")
    auth = {"Authorization": f"Bearer {key}"}
    # 1. Upload the image (JPEG, no alpha) -> a URL the model can read.
    img = Image.open(image).convert("RGB")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    with httpx.Client(timeout=120) as client:
        up = client.post(f"{WAVESPEED}/media/upload/binary", headers=auth,
                         files={"file": (f"{image.stem}.jpg", buf.getvalue(), "image/jpeg")})
        image_url = ((up.json().get("data") or {}).get("download_url")) if up.status_code == 200 else None
        if not image_url:
            raise RuntimeError(f"WaveSpeed upload failed {up.status_code}: {up.text[:200]}")
        # 2. Submit the Wan 2.6 Flash job (silent).
        body = {"prompt": prompt[:1500], "image": image_url, "negative_prompt": NEGATIVE,
                "resolution": "1080p" if resolution.lower().startswith("1080") else "720p",
                "duration": max(2, min(15, int(seconds + 0.999))), "shot_type": "single",
                "enable_prompt_expansion": True, "generate_audio": False, "seed": -1}
        r = client.post(f"{WAVESPEED}/alibaba/wan-2.6/image-to-video-flash", headers=auth, json=body)
        data = r.json()
        data = data.get("data", data)
        job_id = data.get("id")
        if r.status_code != 200 or not job_id:
            raise RuntimeError(f"WaveSpeed error {r.status_code}: {str(data)[:300]}")
        # 3. Poll until done (start fast, then every 5 s; up to ~20 minutes).
        for i in range(260):
            time.sleep(2 if i < 15 else 5)
            res = client.get(f"{WAVESPEED}/predictions/{job_id}/result", headers=auth).json()
            res = res.get("data", res)
            status = str(res.get("status", "")).lower()
            if status == "completed":
                outputs = res.get("outputs") or []
                url = outputs[0] if isinstance(outputs, list) and outputs else outputs
                if isinstance(url, dict):
                    url = url.get("url") or next(iter(url.values()), None)
                if not url:
                    raise RuntimeError(f"WaveSpeed finished without a video URL: {str(res)[:200]}")
                out.write_bytes(client.get(url, follow_redirects=True).content)  # links expire: save now
                return out
            if status in ("failed", "cancelled", "timeout", "deleted"):
                raise RuntimeError(f"WaveSpeed job {status}: {res.get('error') or ''}"[:300])
    raise RuntimeError("WaveSpeed is still rendering after 20 minutes")


def _wan_alibaba(image: Path, prompt: str, seconds: float, out: Path, resolution: str = "720p") -> Path:
    import httpx
    cfg = _dashscope_env()
    if not cfg["key"]:
        raise RuntimeError("DASHSCOPE_API_KEY is not set in .env")
    duration = max(2, min(15, int(seconds + 0.999)))  # whole seconds, 2-15
    body = {
        "model": cfg["model"],
        "input": {"prompt": prompt[:1500], "img_url": _as_data_uri(image),
                  "negative_prompt": NEGATIVE},
        "parameters": {"resolution": "1080P" if resolution.lower().startswith("1080") else "720P",
                       "duration": duration, "audio": False, "prompt_extend": True, "watermark": False},
    }
    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{cfg['base']}/api/v1/services/aigc/video-generation/video-synthesis", headers=headers, json=body)
        data = r.json()
        task_id = (data.get("output") or {}).get("task_id")
        if r.status_code != 200 or not task_id:
            raise RuntimeError(f"Wan error {r.status_code}: {data.get('code')} {data.get('message') or data}"[:300])
        for _ in range(80):  # Alibaba suggests polling ~every 15s; up to 20 minutes
            time.sleep(15)
            t = client.get(f"{cfg['base']}/api/v1/tasks/{task_id}",
                           headers={"Authorization": f"Bearer {cfg['key']}"}).json().get("output") or {}
            status = t.get("task_status")
            if status == "SUCCEEDED" and t.get("video_url"):
                out.write_bytes(client.get(t["video_url"], follow_redirects=True).content)  # URL expires in 24h
                return out
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise RuntimeError(f"Wan task {status}: {t.get('code', '')} {t.get('message', '')}"[:300])
    raise RuntimeError("Wan is still rendering after 20 minutes")


def omni_clip(image: Path, prompt: str, out: Path, resolution: str = "720p") -> Path:
    """Gemini Omni Flash image-to-video (Google's replacement for Veo 3.1 in the Gemini API).
    Paid only (no free tier); needs google-genai with the Interactions API."""
    from config.settings import settings
    from google import genai

    client = genai.Client(api_key=(settings.GEMINI_API_KEY or "").strip("\"'"))
    if not hasattr(client, "interactions"):
        raise RuntimeError("google-genai is too old for Gemini Omni Flash (pip install -U google-genai)")
    data = image.read_bytes()
    mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
    interaction = client.interactions.create(
        model="gemini-omni-1.1-flash",
        input=[{"type": "image", "data": base64.b64encode(data).decode(), "mime_type": mime},
               {"type": "text", "text": prompt + " No dialogue, no voice, no on-screen text."}],
        response_format={"type": "video", "aspect_ratio": "16:9", "resolution": resolution, "delivery": "uri"},
    )
    video = getattr(interaction, "output_video", None)
    if video is not None and getattr(video, "data", None):
        out.write_bytes(base64.b64decode(video.data))
        return out
    file = getattr(video, "file", None) or getattr(video, "uri", None) or video
    for _ in range(60):  # wait for the file to be ready (up to ~5 min)
        name = getattr(file, "name", None) or str(file)
        state = getattr(client.files.get(name=name), "state", "ACTIVE")
        if str(state).endswith("ACTIVE"):
            break
        time.sleep(5)
    out.write_bytes(client.files.download(file=file))
    return out


# ----------------------------------------------------------------------------- subtitles
def _font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONT_PATHS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def caption_chunks(script: str, max_words: int = 4, max_chars: int = 24) -> List[str]:
    """Short, punchy caption lines (Reels style): each sentence/clause is split into evenly sized chunks
    ("This is the" / "split screen test." rather than "This is the split" / "screen test.")."""
    clauses = [c for c in re.split(r"(?<=[.!?,;:])\s+", " ".join(script.split())) if c]
    chunks: List[str] = []
    for clause in clauses:
        words = clause.split()
        n = max(1, -(-len(words) // max_words), -(-len(clause) // max_chars))
        size = -(-len(words) // n)
        chunks += [" ".join(words[k:k + size]) for k in range(0, len(words), size)]
    merged: List[str] = []  # a lone word ("Hi,") flashes too fast: join it to the next chunk if it fits
    for c in chunks:
        if merged and len(merged[-1].split()) == 1 and len(merged[-1].split()) + len(c.split()) <= max_words + 1                 and len(merged[-1]) + 1 + len(c) <= max_chars + 4:
            merged[-1] = f"{merged[-1]} {c}"
        else:
            merged.append(c)
    return merged


def speech_span(media: Path) -> Tuple[float, float]:
    """When he starts and stops talking (FFmpeg silencedetect), so captions don't run over the silence."""
    total = media_duration(media)
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(media), "-af", "silencedetect=n=-35dB:d=0.25",
                           "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    begin = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    finish = starts[-1] if starts and (not ends or starts[-1] > ends[-1]) and starts[-1] > begin + 0.5 else total
    return begin, finish


def caption_timeline(script: str, media: Path) -> List[Tuple[float, float, str]]:
    """(start, end, text) per caption, spread over his actual speech, weighted by length + pauses."""
    chunks = caption_chunks(script)
    if not chunks:
        return []
    begin, finish = speech_span(media)
    weights = [len(c) + 3 + (4 if re.search(r"[.!?]$", c) else 0) for c in chunks]
    unit = (finish - begin) / sum(weights)
    out, t = [], begin
    for c, w in zip(chunks, weights):
        out.append((t, t + w * unit, c))
        t += w * unit
    return out


def render_caption(text: str, path: Path) -> Path:
    """One caption as a transparent PNG, no box: white bold words (key word in yellow) with a thick
    black outline and a soft drop shadow, so they read on any video background."""
    from PIL import ImageFilter
    font = _font(CAPTION_SIZE)
    words = text.split()
    plain = [re.sub(r"[^\w']", "", w).lower() for w in words]
    candidates = [k for k, w in enumerate(plain) if w not in STOPWORDS] or list(range(len(words)))
    key = max(candidates, key=lambda k: len(plain[k]))  # highlight the most meaningful word
    space = font.getlength(" ")
    widths = [font.getlength(w) for w in words]
    text_w = sum(widths) + space * (len(words) - 1)
    ascent, descent = font.getmetrics()
    pad = CAPTION_STROKE + 16
    size = (int(text_w + 2 * pad), ascent + descent + 2 * pad)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))  # blurred dark copy, offset down: gives depth
    sd = ImageDraw.Draw(shadow)
    x = pad
    for word, wd in zip(words, widths):
        sd.text((x + 3, pad + 5), word, font=font, fill=(0, 0, 0, 200), stroke_width=CAPTION_STROKE, stroke_fill=(0, 0, 0, 200))
        x += wd + space
    img = shadow.filter(ImageFilter.GaussianBlur(6))

    d = ImageDraw.Draw(img)
    x = pad
    for k, (word, wd) in enumerate(zip(words, widths)):
        d.text((x, pad), word, font=font, fill=CAPTION_ACCENT if k == key else CAPTION_TEXT,
               stroke_width=CAPTION_STROKE, stroke_fill=(0, 0, 0, 255))
        x += wd + space
    img.save(path)
    return path


# ----------------------------------------------------------------------------- composer
def compose(top_clips: List[Path], durations: List[float], avatar: Path, out: Path, work: Path,
            script: str = "", bottom_ratio: float = BOTTOM_RATIO) -> Path:
    """Content clips on top (65%), the avatar take on the bottom (35%), cross-faded at the split,
    captions just above the clone, deep-black fades at the outer edges."""
    work.mkdir(parents=True, exist_ok=True)
    split, top_h, bot_h = _layout(bottom_ratio)
    top_mask = _mask(work / "mask_top.png", top_h, fade_bottom=True)
    bot_mask = _mask(work / "mask_bottom.png", bot_h, fade_bottom=False)  # vflipped: fades at the bottom edge
    captions = caption_timeline(script, avatar) if script else []

    total = sum(durations)
    args: List[str] = []
    for clip, d in zip(top_clips, durations):  # loop a clip if it's shorter than its scene, cut if longer
        args += ["-stream_loop", "-1", "-t", f"{d:.3f}", "-i", str(clip)]
    n = len(top_clips)
    args += ["-i", str(avatar), "-loop", "1", "-i", str(top_mask), "-loop", "1", "-i", str(bot_mask)]
    ia, itm, ibm = n, n + 1, n + 2
    cap_inputs = []
    for k, (_, _, text) in enumerate(captions):
        args += ["-loop", "1", "-i", str(render_caption(text, work / f"cap{k}.png"))]
        cap_inputs.append(n + 3 + k)

    cover = f"scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h},setsar=1,fps={FPS}"
    f = [f"[{i}:v]{cover},trim=duration={durations[i]:.3f},setpts=PTS-STARTPTS[t{i}]" for i in range(n)]
    f.append("".join(f"[t{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[topraw]")
    f.append(f"[{itm}:v]format=gray,trim=duration={total:.3f}[mt]")
    f.append("[topraw]format=rgba[topa];[topa][mt]alphamerge[top]")
    # Clone: fill the bottom part, cropped around his face (head kept clear of the top edge).
    f.append(f"[{ia}:v]scale={W}:{bot_h}:force_original_aspect_ratio=increase,"
             f"crop={W}:{bot_h}:(iw-{W})/2:(ih-{bot_h})*0.15,setsar=1,fps={FPS},format=rgba[bot0]")
    f.append(f"[{ibm}:v]format=gray,vflip,trim=duration={total:.3f}[mb]")
    f.append("[bot0][mb]alphamerge[bot]")
    f.append(f"color=c=black:s={W}x{H}:r={FPS}:d={total:.3f},format=rgba[bg]")
    f.append(f"[bg][bot]overlay=0:{H - bot_h}:shortest=1:format=auto[b1]")
    last = "b2"
    f.append(f"[b1][top]overlay=0:0:shortest=1:format=auto[{last}]")
    for k, ((start, end, _), idx) in enumerate(zip(captions, cap_inputs)):
        nxt = f"c{k}"
        f.append(f"[{last}][{idx}:v]overlay=(W-w)/2:{split - 30}-h:shortest=1:format=auto:"
                 f"enable='between(t,{start:.3f},{end:.3f})'[{nxt}]")
        last = nxt
    f.append(f"[{last}]format=yuv420p[v]")

    _run([*args, "-filter_complex", ";".join(f), "-map", "[v]", "-map", f"{ia}:a?",
          "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    return out
