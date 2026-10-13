"""Instagram 3:4 Carousel Slide Generator for AI News Updates.

Produces 4 high-resolution (1080x1440, exact 3:4 portrait ratio) carousel slides:
- Slide 1: Attractive Hero Context Image + Bold Headline + Hook Card
- Slide 2: System Architecture / Update Graphic + "The Update" Detailed Explanation Card
- Slide 3: Capabilities & Metrics Graphic + "Key Takeaways" (3 Structured Cards)
- Slide 4: Global Transformation / Future Graphic + "Why It Matters" + Follow CTA Card

Watermark: @ashok_a.i
"""

import math
import os
from pathlib import Path
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config.settings import settings
from src.models.news import PrioritizedNewsItem

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CAROUSEL_OUTPUT_DIR = BASE_DIR / "artifacts" / "carousels"
VISUALS_DIR = BASE_DIR / "assets" / "category_visuals"

# Brand constants
WATERMARK = "@ashok_a.i"
CTA_TEXT = f"Follow {WATERMARK} for Daily AI Updates"
CTA_SUB = "Save this post & share with your network"

# Color Palette — Dark Mode Cyber Tech Aesthetic
BG_DARK = (13, 17, 23)
CARD_BG = (22, 27, 34, 235)
CARD_BG_SOLID = (22, 27, 34)
CYAN = (56, 189, 248)
AMBER = (250, 204, 21)
GREEN = (34, 197, 94)
PURPLE = (168, 85, 247)
MUTED = (148, 163, 184)
BORDER_MUTED = (51, 65, 85, 200)
WHITE = (241, 245, 249)
NEAR_WHITE = (255, 255, 255)

# Category visual mapping for Slide 1 hero context
CATEGORY_HEROES = {
    "robotics": ("ROBOTICS & 3D AI", GREEN, "robotics.jpg"),
    "3d": ("ROBOTICS & 3D AI", GREEN, "robotics.jpg"),
    "physical": ("ROBOTICS & 3D AI", GREEN, "robotics.jpg"),
    "agent": ("AGENT WORKFLOW", PURPLE, "agentic_workflow.jpg"),
    "workflow": ("AGENT WORKFLOW", PURPLE, "agentic_workflow.jpg"),
    "autonomous": ("AGENT WORKFLOW", PURPLE, "agentic_workflow.jpg"),
    "vision": ("COMPUTER VISION", CYAN, "computer_vision.jpg"),
    "multimodal": ("MULTIMODAL AI", CYAN, "computer_vision.jpg"),
    "diffusion": ("GENERATIVE AI", AMBER, "generative_ai.jpg"),
    "generative": ("GENERATIVE AI", AMBER, "generative_ai.jpg"),
    "tool": ("NEW AI TOOL", AMBER, "generative_ai.jpg"),
    "llm": ("FRONTIER MODEL", CYAN, "frontier_model.jpg"),
    "frontier": ("FRONTIER MODEL", CYAN, "frontier_model.jpg"),
    "hardware": ("AI HARDWARE", AMBER, "robotics.jpg"),
    "open source": ("OPEN SOURCE AI", GREEN, "frontier_model.jpg"),
}


def _get_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    """Load best available system font cross-platform (Windows & Linux/Docker)."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """Break text into lines that strictly fit within max_width in pixels."""
    words = text.split()
    if not words:
        return []

    lines = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip() if current_line else word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)
    return lines


def _draw_wrapped_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
                       x: int, y: int, max_width: int, fill, spacing: int = 14, max_lines: int = 0) -> int:
    """Draw word-wrapped text bounded by max_width. Returns rendered height."""
    lines = _wrap_text_lines(draw, text, font, max_width)
    if not lines:
        return 0

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        if len(lines[-1]) > 3:
            lines[-1] = lines[-1][:-3].rstrip() + "..."

    rendered = "\n".join(lines)
    draw.multiline_text((x, y), rendered, fill=fill, font=font, spacing=spacing)
    bbox = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=spacing)
    return bbox[3] - bbox[1]


def _draw_background(width: int = 1080, height: int = 1440, accent_color: Tuple[int, int, int] = CYAN) -> Image.Image:
    """Creates a dark tech gradient background with subtle neon radial glow."""
    base = Image.new("RGBA", (width, height), (*BG_DARK, 255))
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow)

    r, g, b = accent_color
    g_draw.ellipse([width - 400, -150, width + 200, 450], fill=(r, g, b, 40))
    g_draw.ellipse([-200, height - 420, 420, height + 200], fill=(56, 189, 248, 30))

    glow = glow.filter(ImageFilter.GaussianBlur(95))
    base.paste(glow, (0, 0), mask=glow)
    return base


def _get_hero_visual(category: str) -> Tuple[str, Tuple[int, int, int], Optional[Path]]:
    """Resolve display label, accent color, and context image path for category."""
    cat_lower = category.lower().strip()
    for key, (label, color, img_file) in CATEGORY_HEROES.items():
        if key in cat_lower:
            p = VISUALS_DIR / img_file
            if not p.exists():
                p = VISUALS_DIR / "default_ai.jpg"
            return label, color, p if p.exists() else None

    fallback = VISUALS_DIR / "default_ai.jpg"
    return category.upper(), CYAN, fallback if fallback.exists() else None


def _embed_visual_card(img: Image.Image, img_path: Optional[Path], x: int, y: int, w: int, h: int,
                       accent: Tuple[int, int, int], label_badge: str = ""):
    """Embeds an image cleanly into the canvas with rounded corners and border."""
    draw = ImageDraw.Draw(img)
    r, g, b = accent

    # Base background for container
    draw.rounded_rectangle([x, y, x + w, y + h], radius=22,
                           fill=(15, 20, 28, 255), outline=(r, g, b, 170), width=2)

    rendered = False
    if img_path and img_path.exists():
        try:
            sub = Image.open(img_path).convert("RGBA")
            aspect = sub.width / sub.height
            target_aspect = w / h

            if aspect > target_aspect:
                new_h = h
                new_w = int(new_h * aspect)
            else:
                new_w = w
                new_h = int(new_w / aspect)

            sub = sub.resize((new_w, new_h), Image.Resampling.LANCZOS)
            crop_x = (new_w - w) // 2
            crop_y = (new_h - h) // 2
            sub = sub.crop((crop_x, crop_y, crop_x + w, crop_y + h))

            mask = Image.new("L", (w, h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([0, 0, w, h], radius=20, fill=255)

            img.paste(sub, (x, y), mask)
            draw = ImageDraw.Draw(img)
            rendered = True
        except Exception:
            rendered = False

    if not rendered:
        # High-tech vector fallback
        cx, cy = x + w // 2, y + h // 2
        for rad in [140, 100, 60]:
            draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(r, g, b, 60), width=2)
        draw.line([(cx - 150, cy), (cx + 150, cy)], fill=(r, g, b, 90), width=1)
        draw.line([(cx, cy - 150), (cx, cy + 150)], fill=(r, g, b, 90), width=1)

    # Optional label badge on top-left of image
    if label_badge:
        f_badge = _get_font(18, bold=True)
        b_bbox = draw.textbbox((0, 0), label_badge, font=f_badge)
        b_w = b_bbox[2] - b_bbox[0] + 28
        draw.rounded_rectangle([x + 18, y + 16, x + 18 + b_w, y + 50],
                               radius=10, fill=(13, 17, 23, 220), outline=(r, g, b, 160), width=1)
        draw.text((x + 32, y + 23), label_badge, fill=WHITE, font=f_badge)


def _draw_header(draw: ImageDraw.ImageDraw, current_slide: int, total_slides: int,
                 category: str, accent: Tuple[int, int, int], width: int = 1080):
    """Draws header with dynamic category pill and slide counter."""
    f_cat = _get_font(22, bold=True)
    cat_display = category.upper()

    cat_bbox = draw.textbbox((0, 0), cat_display, font=f_cat)
    text_w = cat_bbox[2] - cat_bbox[0]
    pill_w = max(180, min(text_w + 48, width - 300))

    r, g, b = accent
    draw.rounded_rectangle([60, 50, 60 + pill_w, 104], radius=16,
                           fill=(22, 27, 34, 230), outline=(r, g, b, 200), width=2)
    cat_x = 60 + (pill_w - text_w) // 2
    draw.text((cat_x, 64), cat_display, fill=accent, font=f_cat)

    f_counter = _get_font(22, bold=True)
    count_text = f"{current_slide:02d} / {total_slides:02d}"
    draw.rounded_rectangle([width - 190, 50, width - 60, 104], radius=16,
                           fill=(22, 27, 34, 180), outline=(148, 163, 184, 120), width=2)
    draw.text((width - 168, 64), count_text, fill=(226, 232, 240), font=f_counter)
    draw.line([(60, 130), (width - 60, 130)], fill=BORDER_MUTED, width=2)


def _draw_footer(draw: ImageDraw.ImageDraw, current_slide: int, total_slides: int,
                 width: int = 1080, height: int = 1440):
    """Draws clean footer with @ashok_a.i watermark and swipe/save indicator."""
    draw.line([(60, height - 105), (width - 60, height - 105)], fill=BORDER_MUTED, width=2)
    f_foot = _get_font(26, bold=True)
    draw.text((65, height - 76), WATERMARK, fill=(203, 213, 225), font=f_foot)

    if current_slide < total_slides:
        draw.text((width - 225, height - 76), "SWIPE -->", fill=AMBER, font=f_foot)
    else:
        draw.text((width - 275, height - 76), "SAVE & SHARE", fill=CYAN, font=f_foot)


class CarouselGenerator:
    """Generates a complete 4-slide 3:4 Instagram carousel pack with distinct images on every slide."""

    def __init__(self, output_dir: Path = CAROUSEL_OUTPUT_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.W = 1080
        self.H = 1440
        self.MARGIN = 70
        self.CONTENT_W = self.W - self.MARGIN * 2  # 940px

    def _resolve_custom_image(self, item: PrioritizedNewsItem, fallback_path: Optional[Path]) -> Optional[Path]:
        """Dynamically generates a custom AI image for this specific news story using its image_prompt."""
        if not getattr(item, "image_prompt", None):
            return fallback_path

        news_dir = self.output_dir / item.id
        news_dir.mkdir(parents=True, exist_ok=True)
        custom_path = news_dir / "custom_story_hero.jpg"

        if custom_path.exists() and custom_path.stat().st_size > 5000:
            return custom_path

        try:
            import urllib.parse
            import httpx
            # Refine prompt for high-contrast dark tech aesthetic
            clean_prompt = f"{item.image_prompt}, dark cyberspace studio, neon glow, octane render, 8k, no text"
            encoded_prompt = urllib.parse.quote(clean_prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=940&height=560&nologo=true&seed=42"
            with httpx.Client(timeout=14.0, follow_redirects=True) as client:
                res = client.get(url)
                if res.status_code == 200 and len(res.content) > 5000:
                    with open(custom_path, "wb") as f:
                        f.write(res.content)
                    return custom_path
        except Exception:
            pass

        return fallback_path

    def generate_carousel(self, item: PrioritizedNewsItem) -> List[Path]:
        """Creates 4 branded 3:4 Instagram slides for the news item."""
        news_dir = self.output_dir / item.id
        news_dir.mkdir(parents=True, exist_ok=True)

        total = 4
        cat_label, accent, default_hero = _get_hero_visual(item.category)
        hero_img = self._resolve_custom_image(item, default_hero)

        # Visual paths for each distinct slide
        slide2_img = VISUALS_DIR / "slide2_system_breakdown.jpg"
        if not slide2_img.exists():
            slide2_img = VISUALS_DIR / "agentic_workflow.jpg"

        slide3_img = VISUALS_DIR / "slide3_key_takeaways.jpg"
        if not slide3_img.exists():
            slide3_img = VISUALS_DIR / "computer_vision.jpg"

        slide4_img = VISUALS_DIR / "frontier_model.jpg"
        if not slide4_img.exists():
            slide4_img = VISUALS_DIR / "default_ai.jpg"

        slides = [
            self._slide_cover(item, total, cat_label, accent, hero_img),
            self._slide_detail(item, total, cat_label, accent, slide2_img),
            self._slide_takeaways(item, total, cat_label, accent, slide3_img),
            self._slide_why_matters(item, total, cat_label, accent, slide4_img),
        ]

        paths = []
        for i, img in enumerate(slides, 1):
            p = news_dir / f"slide_{i:02d}.png"
            img.convert("RGB").save(p, "PNG")
            paths.append(p)

        return paths

    # ------------------------------------------------------------------
    # Slide 1: Cover — Hero Image + Bold Headline + Hook Card
    # ------------------------------------------------------------------
    def _slide_cover(self, item: PrioritizedNewsItem, total: int, cat_label: str,
                     accent: Tuple[int, int, int], img_path: Optional[Path]) -> Image.Image:
        img = _draw_background(self.W, self.H, accent)
        draw = ImageDraw.Draw(img)
        _draw_header(draw, 1, total, cat_label, accent, self.W)

        M = self.MARGIN
        W_usable = self.CONTENT_W

        # 1. Headline of the update
        f_title = _get_font(52, bold=True)
        title_h = _draw_wrapped_text(draw, item.title, f_title, M, 160,
                                     W_usable, NEAR_WHITE, spacing=14, max_lines=4)

        # 2. Hero Context Image representing the field
        img_top = 160 + title_h + 28
        img_h = 560
        _embed_visual_card(img, img_path, M, img_top, W_usable, img_h, accent, f"DOMAIN: {cat_label}")
        draw = ImageDraw.Draw(img)

        # 3. Breaking Update Hook Card
        hook_top = img_top + img_h + 26
        hook_h = min(220, max(150, self.H - 130 - hook_top))

        draw.rounded_rectangle([M, hook_top, M + W_usable, hook_top + hook_h], radius=20,
                               fill=CARD_BG, outline=BORDER_MUTED, width=2)

        f_label = _get_font(20, bold=True)
        draw.text((M + 30, hook_top + 24), "BREAKING UPDATE", fill=AMBER, font=f_label)

        f_hook = _get_font(26, bold=False)
        hook_text = item.hook or item.summary[:150]
        _draw_wrapped_text(draw, hook_text, f_hook, M + 30, hook_top + 60,
                           W_usable - 60, WHITE, spacing=10, max_lines=3)

        _draw_footer(draw, 1, total, self.W, self.H)
        return img

    # ------------------------------------------------------------------
    # Slide 2: The Update — System Architecture Image + Detailed Breakdown Card
    # ------------------------------------------------------------------
    def _slide_detail(self, item: PrioritizedNewsItem, total: int, cat_label: str,
                      accent: Tuple[int, int, int], img_path: Optional[Path]) -> Image.Image:
        img = _draw_background(self.W, self.H, CYAN)
        draw = ImageDraw.Draw(img)
        _draw_header(draw, 2, total, cat_label, accent, self.W)

        M = self.MARGIN
        W_usable = self.CONTENT_W

        # Section title
        f_sec = _get_font(46, bold=True)
        draw.text((M, 165), "The Update", fill=CYAN, font=f_sec)
        title_bbox = draw.textbbox((0, 0), "The Update", font=f_sec)
        title_w = title_bbox[2] - title_bbox[0]
        draw.line([(M, 230), (M + title_w, 230)], fill=(*CYAN, 160), width=3)

        # 1. Distinct Image for Slide 2 (Architecture / Technical System)
        img_top = 250
        img_h = 420
        _embed_visual_card(img, img_path, M, img_top, W_usable, img_h, CYAN, "ARCHITECTURE & MECHANISM")
        draw = ImageDraw.Draw(img)

        # 2. Text Breakdown Card below the image
        card_top = img_top + img_h + 25
        card_h = self.H - 130 - card_top

        draw.rounded_rectangle([M, card_top, M + W_usable, card_top + card_h], radius=22,
                               fill=CARD_BG, outline=BORDER_MUTED, width=2)

        f_badge = _get_font(20, bold=True)
        draw.text((M + 35, card_top + 28), "HOW IT WORKS", fill=CYAN, font=f_badge)

        f_body = _get_font(30, bold=False)
        _draw_wrapped_text(draw, item.summary, f_body, M + 35, card_top + 80,
                           W_usable - 70, WHITE, spacing=14, max_lines=10)

        _draw_footer(draw, 2, total, self.W, self.H)
        return img

    # ------------------------------------------------------------------
    # Slide 3: Key Takeaways — Benchmark/Feature Graphic + 3 Insight Cards
    # ------------------------------------------------------------------
    def _slide_takeaways(self, item: PrioritizedNewsItem, total: int, cat_label: str,
                         accent: Tuple[int, int, int], img_path: Optional[Path]) -> Image.Image:
        img = _draw_background(self.W, self.H, AMBER)
        draw = ImageDraw.Draw(img)
        _draw_header(draw, 3, total, cat_label, accent, self.W)

        M = self.MARGIN
        W_usable = self.CONTENT_W

        # Section title
        f_sec = _get_font(46, bold=True)
        draw.text((M, 165), "Key Takeaways", fill=AMBER, font=f_sec)
        title_bbox = draw.textbbox((0, 0), "Key Takeaways", font=f_sec)
        title_w = title_bbox[2] - title_bbox[0]
        draw.line([(M, 230), (M + title_w, 230)], fill=(*AMBER, 160), width=3)

        # 1. Distinct Image for Slide 3 (Benchmark / Insights Telemetry)
        img_top = 250
        img_h = 360
        _embed_visual_card(img, img_path, M, img_top, W_usable, img_h, AMBER, "CORE CAPABILITIES & BENCHMARKS")
        draw = ImageDraw.Draw(img)

        # 2. 3 Insight Cards below image
        bullets = list(item.key_bullet_points or [])
        while len(bullets) < 3:
            bullets.append("Key capability advancing modern AI applications.")
        bullets = bullets[:3]

        card_y = img_top + img_h + 25
        card_h = 175
        card_gap = 18

        f_bnum = _get_font(24, bold=True)
        f_btext = _get_font(26, bold=False)

        for idx, bullet in enumerate(bullets, start=1):
            draw.rounded_rectangle([M, card_y, M + W_usable, card_y + card_h], radius=18,
                                   fill=CARD_BG, outline=(*CYAN, 120), width=2)

            # Badge 01, 02, 03
            badge_size = 48
            badge_x = M + 26
            badge_y = card_y + 24
            draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_size, badge_y + badge_size],
                                   radius=12, fill=(*CYAN, 255))
            num_text = f"{idx:02d}"
            num_bbox = draw.textbbox((0, 0), num_text, font=f_bnum)
            num_w = num_bbox[2] - num_bbox[0]
            num_h = num_bbox[3] - num_bbox[1]
            draw.text((badge_x + (badge_size - num_w) // 2, badge_y + (badge_size - num_h) // 2 - 2),
                      num_text, fill=(15, 23, 42), font=f_bnum)

            text_x = badge_x + badge_size + 24
            text_max_w = (M + W_usable) - text_x - 30
            _draw_wrapped_text(draw, bullet, f_btext, text_x, card_y + 24,
                               text_max_w, WHITE, spacing=10, max_lines=3)

            card_y += card_h + card_gap

        _draw_footer(draw, 3, total, self.W, self.H)
        return img

    # ------------------------------------------------------------------
    # Slide 4: Why It Matters — Future Transformation Graphic + Strategic Impact + CTA
    # ------------------------------------------------------------------
    def _slide_why_matters(self, item: PrioritizedNewsItem, total: int, cat_label: str,
                           accent: Tuple[int, int, int], img_path: Optional[Path]) -> Image.Image:
        img = _draw_background(self.W, self.H, GREEN)
        draw = ImageDraw.Draw(img)
        _draw_header(draw, 4, total, cat_label, accent, self.W)

        M = self.MARGIN
        W_usable = self.CONTENT_W

        # Section title
        f_sec = _get_font(46, bold=True)
        draw.text((M, 165), "Why It Matters", fill=GREEN, font=f_sec)
        title_bbox = draw.textbbox((0, 0), "Why It Matters", font=f_sec)
        title_w = title_bbox[2] - title_bbox[0]
        draw.line([(M, 230), (M + title_w, 230)], fill=(*GREEN, 160), width=3)

        # 1. Distinct Image for Slide 4 (Global Transformation / Future Horizon)
        img_top = 250
        img_h = 380
        _embed_visual_card(img, img_path, M, img_top, W_usable, img_h, GREEN, "STRATEGIC TRANSFORMATION")
        draw = ImageDraw.Draw(img)

        # 2. Impact Card
        card_top = img_top + img_h + 25
        card_h = 320
        draw.rounded_rectangle([M, card_top, M + W_usable, card_top + card_h], radius=22,
                               fill=CARD_BG, outline=(*GREEN, 180), width=2)

        f_label = _get_font(18, bold=True)
        draw.text((M + 35, card_top + 26), "INDUSTRY IMPACT", fill=GREEN, font=f_label)

        f_body = _get_font(30, bold=False)
        _draw_wrapped_text(draw, item.why_it_matters, f_body, M + 35, card_top + 65,
                           W_usable - 70, WHITE, spacing=14, max_lines=6)

        # 3. Call To Action Card
        cta_top = card_top + card_h + 25
        cta_h = self.H - 130 - cta_top
        draw.rounded_rectangle([M, cta_top, M + W_usable, cta_top + cta_h], radius=20,
                               fill=(30, 41, 59, 240), outline=(*CYAN, 200), width=2)

        f_cta = _get_font(32, bold=True)
        f_sub = _get_font(20, bold=False)

        cta_bbox = draw.textbbox((0, 0), CTA_TEXT, font=f_cta)
        cta_w = cta_bbox[2] - cta_bbox[0]
        cta_x = M + (W_usable - cta_w) // 2

        sub_bbox = draw.textbbox((0, 0), CTA_SUB, font=f_sub)
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_x = M + (W_usable - sub_w) // 2

        draw.text((cta_x, cta_top + 38), CTA_TEXT, fill=AMBER, font=f_cta)
        draw.text((sub_x, cta_top + 88), CTA_SUB, fill=MUTED, font=f_sub)

        _draw_footer(draw, 4, total, self.W, self.H)
        return img
