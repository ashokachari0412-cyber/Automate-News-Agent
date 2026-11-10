"""Free, locally drawn visuals for Recorded Vid's floating window: animated diagrams, text and logos.

Every visual in one video shares the same look (dark navy card, white text, warm-yellow accent), so the clips
read as one set. Items appear when he says them: each item carries "at", the second of his recording where he
says it, and the renderer turns that into the moment its box, line or logo pops in.

Kinds:
  diagram : 2-4 boxes joined by arrows that draw themselves (a row, or a clockwise loop of 4)
  text    : one big headline (a number counts up), up to 3 short lines under it
  logo    : 1-3 company/product logos in white tiles (real logos fetched by website domain), names below
"""

import io
import logging
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

SS = 2                                  # drawn 2x larger, then halved: smooth edges and text
FPS = 25
BG_TOP, BG_BOTTOM = (12, 17, 34), (22, 30, 58)
BOX_FILL, BOX_LINE = (30, 40, 72), (120, 135, 175)
WHITE, MUTED = (255, 255, 255), (178, 188, 214)
ACCENT = (255, 200, 61)                 # the same warm yellow as the captions' key word
HEAD_FONTS = ["C:/Windows/Fonts/seguibl.ttf", "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf"]
BODY_FONTS = ["C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
POP = 0.35                              # s for an item to pop in
ARROW = 0.30                            # s for an arrow to draw itself (ends as its box appears)

_fonts: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _fonts:
        for f in HEAD_FONTS if kind == "head" else BODY_FONTS:
            if Path(f).exists():
                _fonts[key] = ImageFont.truetype(f, size)
                break
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def _ease(x: float) -> float:
    """0..1 -> 0..1, fast then gentle (ease-out cubic)."""
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _back(x: float) -> float:
    """Ease-out with a small overshoot: the pop of a box or tile."""
    x = max(0.0, min(1.0, x))
    c = 1.6
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> Optional[List[str]]:
    """Greedy word wrap; None if it needs more than max_lines."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines or any(font.getlength(l) > width for l in lines):
        return None
    return lines


def _fit(text: str, kind: str, width: int, height: int, max_size: int, max_lines: int,
         min_size: int = 18) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    """Largest font (<= max_size) whose wrapped text fits the box."""
    size = max_size
    while size > min_size:
        f = _font(kind, size)
        lines = _wrap(text, f, width, max_lines)
        if lines and len(lines) * size * 1.18 <= height:
            return f, lines
        size -= 2
    f = _font(kind, min_size)
    return f, (_wrap(text, f, width, 99) or [text])[:max_lines]


def _text_block(draw: ImageDraw.ImageDraw, lines: List[str], font, cx: float, cy: float, fill) -> None:
    """Lines centred on (cx, cy)."""
    lh = font.size * 1.18
    y = cy - lh * len(lines) / 2
    for line in lines:
        draw.text((cx, y + lh / 2), line, font=font, fill=fill, anchor="mm")
        y += lh


def _background(w: int, h: int) -> Image.Image:
    """Navy gradient with a faint dot grid and a soft glow: the same backdrop for every drawn visual."""
    bg = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(bg)
    for y in range(h):
        k = y / max(1, h - 1)
        px.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse((w * 0.15, -h * 0.35, w * 0.85, h * 0.55), fill=60)
    from PIL import ImageFilter
    glow = glow.filter(ImageFilter.GaussianBlur(w // 8))
    bg = Image.composite(Image.new("RGB", (w, h), (60, 70, 120)), bg, glow)
    dots = ImageDraw.Draw(bg)
    step = 44 * SS
    for y in range(step // 2, h, step):
        for x in range(step // 2, w, step):
            dots.ellipse((x - 2 * SS, y - 2 * SS, x + 2 * SS, y + 2 * SS), fill=(45, 56, 92))
    return bg


def _paste_scaled(frame: Image.Image, layer: Image.Image, cx: float, cy: float, scale: float, alpha: float) -> None:
    """Paste an RGBA layer centred on (cx, cy), scaled and faded."""
    if alpha <= 0.01 or scale <= 0.01:
        return
    if abs(scale - 1) > 0.005:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BILINEAR)
    if alpha < 0.99:
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer = layer.copy()
        layer.putalpha(a)
    frame.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)


def _arrow(draw: ImageDraw.ImageDraw, a: Tuple[float, float], b: Tuple[float, float], k: float, color) -> None:
    """Arrow from a toward b, drawn k (0..1) of the way; the head shows once it is nearly there."""
    if k <= 0:
        return
    x = a[0] + (b[0] - a[0]) * k
    y = a[1] + (b[1] - a[1]) * k
    draw.line([a, (x, y)], fill=color, width=7 * SS)
    if k > 0.6:
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        s = 22 * SS * min(1.0, (k - 0.6) / 0.4)
        draw.polygon([(x + math.cos(ang) * s * 0.4, y + math.sin(ang) * s * 0.4),
                      (x - math.cos(ang - 0.55) * s, y - math.sin(ang - 0.55) * s),
                      (x - math.cos(ang + 0.55) * s, y - math.sin(ang + 0.55) * s)], fill=color)


def _times(items: List[Dict[str, Any]], seconds: float, start: float, lead: float) -> List[float]:
    """When each item appears (seconds into the clip): when he says it, in order, else spread evenly."""
    last = max(lead, seconds - 0.8)
    out, prev = [], lead - 0.6
    n = len(items)
    for i, it in enumerate(items):
        even = lead + (last - lead) * (i / max(1, n - 1) if n > 1 else 0) * 0.75
        try:
            t = float(it.get("at")) - start
        except (TypeError, ValueError):
            t = even
        if not (0 <= t <= seconds):
            t = even
        t = max(t, prev + 0.6, lead)
        out.append(min(t, last))
        prev = out[-1]
    return out


# ----------------------------------------------------------------------------- diagram
def _box_layer(text: str, w: int, h: int, step: int, active: bool) -> Image.Image:
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = 26 * SS
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=BOX_FILL + (255,),
                        outline=(ACCENT if active else BOX_LINE) + (255,), width=(6 if active else 3) * SS)
    badge = 23 * SS
    d.ellipse((18 * SS, 18 * SS, 18 * SS + 2 * badge, 18 * SS + 2 * badge), fill=ACCENT + (255,))
    d.text((18 * SS + badge, 18 * SS + badge), str(step), font=_font("head", 26 * SS), fill=BG_TOP, anchor="mm")
    font, lines = _fit(text, "body", w - 44 * SS, h - 96 * SS, 46 * SS, 3, 20 * SS)
    _text_block(d, lines, font, w / 2, h / 2 + 22 * SS, WHITE)
    return layer


def _diagram_slots(n: int, w: int, h: int, top: int) -> List[Tuple[float, float, int, int]]:
    """(centre x, centre y, box w, box h) per item: a row for 1-3, a clockwise 2x2 loop for 4."""
    margin, gap = 30 * SS, 56 * SS
    avail_h = h - top - 40 * SS
    if n <= 3:
        bw = int((w - 2 * margin - (n - 1) * gap) / n)
        bh = int(min(avail_h * 0.8, bw * 1.3 if n == 3 else bw * 0.8))
        cy = top + avail_h / 2
        return [(margin + bw / 2 + i * (bw + gap), cy, bw, bh) for i in range(n)]
    bw = int((w - 2 * margin - gap) / 2)
    bh = int((avail_h - gap * 0.8) / 2)
    xs = (margin + bw / 2, margin + bw * 1.5 + gap)
    ys = (top + bh / 2, top + bh * 1.5 + gap * 0.8)
    return [(xs[0], ys[0], bw, bh), (xs[1], ys[0], bw, bh), (xs[1], ys[1], bw, bh), (xs[0], ys[1], bw, bh)]


def _edge(a: Tuple[float, float, int, int], b: Tuple[float, float, int, int]) -> Tuple[Tuple, Tuple]:
    """Arrow endpoints just outside both boxes, along the line between their centres."""
    (ax, ay, aw, ah), (bx, by, bw, bh) = a, b
    gap = 8 * SS
    if abs(bx - ax) >= abs(by - ay):
        sx = 1 if bx > ax else -1
        return (ax + sx * (aw / 2 + gap), ay), (bx - sx * (bw / 2 + gap), by)
    sy = 1 if by > ay else -1
    return (ax, ay + sy * (ah / 2 + gap)), (bx, by - sy * (bh / 2 + gap))


def _diagram_frames(data: Dict[str, Any], seconds: float, start: float, w: int, h: int):
    items = [it for it in data.get("items") or [] if str(it.get("text") or "").strip()][:4]
    if not items:
        items = [{"text": data.get("headline") or "..."}]
    title = str(data.get("headline") or "").strip()
    bg = _background(w, h)
    top = 40 * SS
    if title:
        tf, tl = _fit(title, "head", w - 120 * SS, 120 * SS, 54 * SS, 2, 28 * SS)
        top = int(40 * SS + len(tl) * tf.size * 1.18 + 26 * SS)
    slots = _diagram_slots(len(items), w, h, top)
    times = _times(items, seconds, start, lead=0.45 if title else 0.15)
    layers = {(i, act): _box_layer(str(it["text"]).strip(), s[2], s[3], i + 1, act)
              for i, (it, s) in enumerate(zip(items, slots)) for act in (False, True)}
    total = max(1, int(round(seconds * FPS)))
    for f in range(total):
        t = f / FPS
        frame = bg.copy()
        d = ImageDraw.Draw(frame)
        if title:
            k = _ease(t / 0.4)
            _text_block(d, tl, tf, w / 2, 40 * SS + len(tl) * tf.size * 1.18 / 2 + (1 - k) * 20 * SS,
                        tuple(int(c * k + bc * (1 - k)) for c, bc in zip(WHITE, BG_TOP)))
        shown = [i for i, ti in enumerate(times) if t >= ti]
        newest = shown[-1] if shown else -1
        for i in range(1, len(items)):  # arrows draw toward the next box just before it appears
            k = _ease((t - (times[i] - ARROW)) / ARROW)
            if k > 0:
                a, b = _edge(slots[i - 1], slots[i])
                _arrow(d, a, b, k, ACCENT if i == newest else MUTED)
        for i, ti in enumerate(times):
            k = (t - ti) / POP
            if k > 0:
                _paste_scaled(frame, layers[(i, i == newest)], slots[i][0], slots[i][1], 0.8 + 0.2 * _back(k),
                              _ease(k * 1.5))
        yield frame


# ----------------------------------------------------------------------------- text
_NUM = re.compile(r"^([^\d]*)(\d[\d,]*\.?\d*)(.*)$")


def _count(headline: str, k: float) -> str:
    """'31%' at k=0.5 -> '16%': a number in the headline counts up while it pops in."""
    m = _NUM.match(headline)
    if not m or k >= 1:
        return headline
    pre, num, post = m.groups()
    dec = len(num.split(".")[1]) if "." in num else 0
    val = float(num.replace(",", "")) * _ease(k)
    s = f"{val:,.{dec}f}" if "," in num else f"{val:.{dec}f}"
    return pre + s + post


def _text_frames(data: Dict[str, Any], seconds: float, start: float, w: int, h: int):
    headline = str(data.get("headline") or "").strip() or "..."
    subs = [it for it in data.get("items") or [] if str(it.get("text") or "").strip()][:3]
    bg = _background(w, h)
    hf, hl = _fit(headline, "head", w - 120 * SS, int(h * (0.42 if subs else 0.6)), 150 * SS, 2, 40 * SS)
    head_h = len(hl) * hf.size * 1.18
    sf = _font("body", 42 * SS)
    sub_lines = [(_wrap(str(it["text"]).strip(), sf, w - 160 * SS, 2) or [str(it["text"]).strip()]) for it in subs]
    sub_h = sum(len(l) * sf.size * 1.25 + 18 * SS for l in sub_lines)
    block = head_h + (40 * SS + sub_h if subs else 0) + 30 * SS
    y0 = (h - block) / 2
    times = _times(subs, seconds, start, lead=0.9) if subs else []
    total = max(1, int(round(seconds * FPS)))
    has_num = bool(_NUM.match(headline))
    for f in range(total):
        t = f / FPS
        frame = bg.copy()
        d = ImageDraw.Draw(frame)
        k = _back(t / 0.45)
        lines = [_count(l, t / 0.9) if has_num and i == 0 else l for i, l in enumerate(hl)]
        layer = Image.new("RGBA", (w, int(head_h + 20 * SS)), (0, 0, 0, 0))
        _text_block(ImageDraw.Draw(layer), lines, hf, w / 2, layer.height / 2, ACCENT if has_num else WHITE)
        _paste_scaled(frame, layer, w / 2, y0 + layer.height / 2, 0.75 + 0.25 * k, _ease(t / 0.3))
        bar = _ease((t - 0.25) / 0.5) * 180 * SS  # accent underline grows under the headline
        if bar > 1:
            by = y0 + head_h + 26 * SS
            d.rounded_rectangle((w / 2 - bar / 2, by, w / 2 + bar / 2, by + 9 * SS), radius=5 * SS, fill=ACCENT)
        y = y0 + head_h + 70 * SS
        for lines_i, ti in zip(sub_lines, times):
            kk = _ease((t - ti) / POP)
            lh = sf.size * 1.25
            if kk > 0:
                col = tuple(int(c * kk + bc * (1 - kk)) for c, bc in zip(WHITE, BG_BOTTOM))
                _text_block(d, lines_i, sf, w / 2, y + lh * len(lines_i) / 2 + (1 - kk) * 24 * SS, col)
            y += lh * len(lines_i) + 18 * SS
        yield frame


# ----------------------------------------------------------------------------- logos
def fetch_logo(domain: str, cache: Path) -> Optional[Image.Image]:
    """The brand's real logo by its website (Google's favicon service, 256 px), cached; None if not found."""
    domain = re.sub(r"^https?://", "", str(domain or "").strip().lower()).split("/")[0]
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return None
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{domain}.png"
    if not path.exists():
        import httpx
        try:
            r = httpx.get("https://www.google.com/s2/favicons", params={"domain": domain, "sz": 256},
                          timeout=15, follow_redirects=True)
            img = Image.open(io.BytesIO(r.content)).convert("RGBA") if r.status_code == 200 else None
            if not img or img.width < 64:  # the 16 px globe = no real logo
                return None
            img.save(path)
        except Exception as e:
            logger.warning(f"Logo for {domain} not found: {e}")
            return None
    return Image.open(path).convert("RGBA")


def _tile(name: str, logo: Optional[Image.Image], size: int) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=(255, 255, 255, 255))
    if logo:
        inner = int(size * 0.66)
        lg = logo.copy()
        lg.thumbnail((inner, inner), Image.LANCZOS)
        if lg.width < inner and lg.height < inner:  # small icons are scaled up to fill the tile
            s = inner / max(lg.width, lg.height)
            lg = lg.resize((int(lg.width * s), int(lg.height * s)), Image.LANCZOS)
        layer.paste(lg, ((size - lg.width) // 2, (size - lg.height) // 2), lg)
    else:  # no logo found: the brand's initial
        d.text((size / 2, size / 2), (name[:1] or "?").upper(), font=_font("head", int(size * 0.5)),
               fill=BG_TOP, anchor="mm")
    return layer


def _logo_frames(data: Dict[str, Any], seconds: float, start: float, w: int, h: int, cache: Path):
    items = [it for it in data.get("items") or [] if str(it.get("text") or "").strip()][:3] or \
        [{"text": data.get("headline") or "?"}]
    title = str(data.get("headline") or "").strip()
    bg = _background(w, h)
    n = len(items)
    size = int(min((w - 80 * SS - (n - 1) * 60 * SS) / n, h * 0.42))
    gap = 60 * SS
    x0 = (w - (n * size + (n - 1) * gap)) / 2 + size / 2
    tf = tl = None
    if title:
        tf, tl = _fit(title, "head", w - 120 * SS, 110 * SS, 50 * SS, 2, 26 * SS)
    nf = _font("body", 40 * SS)
    cy = h * (0.52 if title else 0.45)
    tiles = [_tile(str(it["text"]), fetch_logo(it.get("domain") or "", cache), size) for it in items]
    times = _times(items, seconds, start, lead=0.4 if title else 0.1)
    total = max(1, int(round(seconds * FPS)))
    for f in range(total):
        t = f / FPS
        frame = bg.copy()
        d = ImageDraw.Draw(frame)
        if title:
            k = _ease(t / 0.4)
            _text_block(d, tl, tf, w / 2, 40 * SS + len(tl) * tf.size * 1.18 / 2 + (1 - k) * 20 * SS,
                        tuple(int(c * k + bc * (1 - k)) for c, bc in zip(WHITE, BG_TOP)))
        for i, (it, tile, ti) in enumerate(zip(items, tiles, times)):
            k = (t - ti) / POP
            if k <= 0:
                continue
            x = x0 + i * (size + gap)
            _paste_scaled(frame, tile, x, cy, 0.7 + 0.3 * _back(k), _ease(k * 1.5))
            kk = _ease((t - ti - 0.15) / POP)
            name = str(it["text"]).strip()
            col = tuple(int(c * kk + bc * (1 - kk)) for c, bc in zip(WHITE, BG_BOTTOM))
            font, lines = _fit(name, "body", size + gap - 10 * SS, 110 * SS, 40 * SS, 2, 22 * SS)
            _text_block(d, lines, font, x, cy + size / 2 + 30 * SS + len(lines) * font.size * 0.6, col)
        yield frame


# ----------------------------------------------------------------------------- render
def render_visual(kind: str, data: Dict[str, Any], seconds: float, start: float, size: Tuple[int, int],
                  out: Path, cache: Path) -> Path:
    """One clip's drawn visual as an MP4 (no audio). start = where the clip begins in his recording (the
    items' "at" times are seconds of his recording)."""
    from src.video.split_video import ffmpeg_exe
    w, h = size[0] * SS, size[1] * SS
    frames = {"diagram": lambda: _diagram_frames(data, seconds, start, w, h),
              "text": lambda: _text_frames(data, seconds, start, w, h),
              "logo": lambda: _logo_frames(data, seconds, start, w, h, cache)}[kind]()
    proc = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
                             str(out)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in frames:
            proc.stdin.write(frame.reduce(SS).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="ignore")
    if proc.wait() != 0:
        raise RuntimeError(f"FFmpeg failed drawing the {kind} visual: {err[-400:]}")
    return out
