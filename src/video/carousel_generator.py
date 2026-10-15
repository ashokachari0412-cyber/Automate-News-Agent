"""Instagram creator-style carousel generator for AI news updates.

Produces 4 portrait posts (1080x1350, Instagram's 4:5 format). Each post is a
full-bleed realistic photo with bold overlay text on a dark bottom fade:
- Post 1: Cover headline + hook
- Post 2: What's new
- Post 3: Key takeaways
- Post 4: Why it matters + follow CTA

Watermark: @ashok_a.i
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.models.news import PrioritizedNewsItem

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CAROUSEL_OUTPUT_DIR = BASE_DIR / "artifacts" / "carousels"
VISUALS_DIR = BASE_DIR / "assets" / "category_visuals"

WATERMARK = "@ashok_a.i"
CTA_TEXT = f"Follow {WATERMARK} for daily AI updates"

WHITE = (255, 255, 255)
SOFT_WHITE = (226, 232, 240)
ACCENT = (250, 204, 21)  # creator-style yellow highlight
LABELS = ["AI NEWS", "WHAT'S NEW", "KEY TAKEAWAYS", "WHY IT MATTERS"]

# Emoji / pictograph ranges: post fonts can't render these (they show as boxes)
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\uFE0F\u200D\u20E3]+"
)


def _strip_emojis(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _EMOJI_RE.sub("", text or "")).strip()


def _get_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    """Load best available system font cross-platform (Windows & Linux)."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    lines: List[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            test = f"{current} {word}".strip()
            if draw.textlength(test, font=font) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _fallback_photo(category: str) -> Optional[Path]:
    cat = (category or "").lower()
    mapping = {"robot": "robotics.jpg", "vision": "computer_vision.jpg", "agent": "agentic_workflow.jpg",
               "generative": "generative_ai.jpg", "llm": "frontier_model.jpg"}
    for k, f in mapping.items():
        if k in cat and (VISUALS_DIR / f).exists():
            return VISUALS_DIR / f
    p = VISUALS_DIR / "default_ai.jpg"
    return p if p.exists() else None


class CarouselGenerator:
    """Generates a 4-image Instagram carousel of realistic photo posts with overlay text."""

    def __init__(self, output_dir: Path = CAROUSEL_OUTPUT_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.W = 1080
        self.H = 1350
        self.M = 72

    async def generate_carousel_async(self, item: PrioritizedNewsItem) -> List[Path]:
        """Plans + renders story-specific photos (Gemini-directed), then composes the posts."""
        from src.video.slide_visuals import prepare_post_assets
        posts, photos = await prepare_post_assets(item)
        news_dir = self.output_dir / item.id
        news_dir.mkdir(parents=True, exist_ok=True)
        (news_dir / "assets.json").write_text(
            json.dumps({"posts": posts, "photos": [str(p) if p else None for p in photos]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return await asyncio.to_thread(self.generate_carousel, item, posts, photos)

    def generate_carousel(self, item: PrioritizedNewsItem,
                          posts: Optional[List[Dict[str, str]]] = None,
                          photos: Optional[List[Optional[Path]]] = None) -> List[Path]:
        news_dir = self.output_dir / item.id
        news_dir.mkdir(parents=True, exist_ok=True)

        if posts is None or photos is None:
            saved = news_dir / "assets.json"
            if saved.exists():
                data = json.loads(saved.read_text(encoding="utf-8"))
                posts = data["posts"]
                photos = [Path(p) if p else None for p in data["photos"]]
            else:
                posts = [
                    {"headline": item.title, "text": item.hook or item.summary},
                    {"headline": "What's new", "text": item.summary},
                    {"headline": "Key takeaways", "text": "\n".join((item.key_bullet_points or [])[:3])},
                    {"headline": "Why it matters", "text": item.why_it_matters},
                ]
                photos = [None] * 4

        fallback = _fallback_photo(item.category)
        total = len(posts)
        paths = []
        for i, post in enumerate(posts, 1):
            photo = photos[i - 1] if photos[i - 1] and photos[i - 1].exists() else fallback
            img = self._compose(photo, post, i, total)
            p = news_dir / f"slide_{i:02d}.png"
            img.save(p, "PNG")
            paths.append(p)
        return paths

    def _compose(self, photo: Optional[Path], post: Dict[str, str], index: int, total: int) -> Image.Image:
        W, H, M = self.W, self.H, self.M

        # 1. Full-bleed photo (cover-cropped to 4:5)
        if photo:
            base = ImageOps.fit(Image.open(photo).convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.35))
        else:
            base = Image.new("RGB", (W, H), (24, 24, 28))
        img = base.convert("RGBA")

        # 2. Dark fade at the bottom for legible text, light fade at the top for the handle
        fade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fade)
        start = int(H * 0.38)
        for y in range(start, H):
            t = (y - start) / (H - start)
            fd.line([(0, y), (W, y)], fill=(0, 0, 0, int(235 * min(1.0, t * 1.35))))
        for y in range(0, 170):
            fd.line([(0, y), (W, y)], fill=(0, 0, 0, int(120 * (1 - y / 170))))
        img = Image.alpha_composite(img, fade)
        draw = ImageDraw.Draw(img)

        # 3. Top bar: handle + page indicator
        f_small = _get_font(30, bold=True)
        draw.text((M, 52), WATERMARK, fill=WHITE, font=f_small)
        counter = f"{index}/{total}"
        draw.text((W - M - draw.textlength(counter, font=f_small), 52), counter, fill=WHITE, font=f_small)

        # 4. Bottom text block, laid out bottom-up
        headline = _strip_emojis(post.get("headline", ""))
        body = _strip_emojis(post.get("text", ""))
        is_cover = index == 1
        is_last = index == total
        is_list = index == 3 and "\n" in body

        f_head = _get_font(72 if is_cover else 60, bold=True)
        f_body = _get_font(34, bold=False)
        f_label = _get_font(26, bold=True)
        max_w = W - 2 * M

        head_lines = _wrap(draw, headline, f_head, max_w)[:4]
        if is_list:
            body_lines = [f"-  {ln.strip().lstrip('-•*').strip()}" for ln in body.split("\n") if ln.strip()][:3]
            body_lines = [w for ln in body_lines for w in _wrap(draw, ln, f_body, max_w)][:7]
        else:
            body_lines = _wrap(draw, body, f_body, max_w)[:5]

        head_lh = int(f_head.size * 1.12)
        body_lh = int(f_body.size * 1.4)
        footer_h = 70
        block_h = 44 + len(head_lines) * head_lh + 22 + len(body_lines) * body_lh
        y = H - footer_h - 40 - block_h

        # Label pill
        label = LABELS[min(index, len(LABELS)) - 1]
        lw = draw.textlength(label, font=f_label)
        draw.rounded_rectangle([M, y, M + lw + 32, y + 42], radius=21, fill=ACCENT)
        draw.text((M + 16, y + 6), label, fill=(17, 17, 17), font=f_label)
        y += 44 + 14

        for ln in head_lines:
            draw.text((M, y), ln, fill=WHITE, font=f_head)
            y += head_lh
        y += 14
        for ln in body_lines:
            draw.text((M, y), ln, fill=SOFT_WHITE, font=f_body)
            y += body_lh

        # 5. Footer: swipe hint or follow CTA
        f_foot = _get_font(28, bold=True)
        foot = CTA_TEXT if is_last else "Swipe for more  >"
        fw = draw.textlength(foot, font=f_foot)
        draw.text(((W - fw) / 2 if is_last else W - M - fw, H - footer_h), foot,
                  fill=ACCENT if is_last else WHITE, font=f_foot)

        return img.convert("RGB")
