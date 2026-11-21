"""Title cards rendered with Pillow in the brand colours."""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from demo_gen.script import Brand


def _font(brand: Brand, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [brand.font] if brand.font else []
    candidates += [
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for path in candidates:
        if path and path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def title_card(
    title: str, subtitle: str | None, size: tuple[int, int], brand: Brand, out: Path
) -> Path:
    w, h = size
    image = Image.new("RGB", size, brand.background)
    draw = ImageDraw.Draw(image)
    title_size = int(min(w, h) * 0.09)
    wrap = 18 if w < h else 26
    lines = textwrap.wrap(title, wrap)
    title_font = _font(brand, title_size)
    sub_font = _font(brand, int(title_size * 0.42))
    line_h = int(title_size * 1.15)
    block = line_h * len(lines) + (int(title_size * 0.9) if subtitle else 0)
    y = (h - block) // 2
    margin = int(w * 0.08)
    draw.rectangle(
        [
            margin,
            y - int(title_size * 0.5),
            margin + int(title_size * 1.2),
            y - int(title_size * 0.38),
        ],
        fill=brand.accent,
    )
    for line in lines:
        draw.text((margin, y), line, font=title_font, fill=brand.foreground)
        y += line_h
    if subtitle:
        draw.text((margin, y + int(title_size * 0.3)), subtitle, font=sub_font, fill=brand.accent)
    image.save(out)
    return out
