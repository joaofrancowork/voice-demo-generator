"""Burned-in captions as a transparent video track.

Rendering captions with Pillow instead of ffmpeg's libass filter means the pipeline
works with any ffmpeg build (Homebrew's default has no libass) and gives full
control over the look: one frame per spoken word, with that word highlighted.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from demo_gen.captions import Word
from demo_gen.cards import _font
from demo_gen.ffmpeg import FPS, run
from demo_gen.script import Brand


def _caption_image(
    line: list[Word], active: int, size: tuple[int, int], brand: Brand, out: Path
) -> Path:
    w, h = size
    vertical = h > w
    font = _font(brand, 76 if vertical else 60)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    texts = [word.text for word in line]
    space = draw.textlength(" ", font=font)
    widths = [draw.textlength(t, font=font) for t in texts]
    total = sum(widths) + space * (len(texts) - 1)
    x = (w - total) / 2
    y = h * (0.72 if vertical else 0.84)
    pad = 22
    box_h = font.size if hasattr(font, "size") else 40
    draw.rounded_rectangle(
        [x - pad, y - pad * 0.6, x + total + pad, y + box_h + pad * 0.9],
        radius=18,
        fill=(0, 0, 0, 170),
    )
    for i, (text, width) in enumerate(zip(texts, widths, strict=True)):
        colour = brand.accent if i == active else brand.foreground
        draw.text((x, y), text, font=font, fill=colour, stroke_width=2, stroke_fill="#000000")
        x += width + space
    image.save(out)
    return out


def caption_track(
    lines: list[list[Word]],
    total_seconds: float,
    size: tuple[int, int],
    brand: Brand,
    workdir: Path,
) -> Path:
    """Encode a transparent caption track covering ``total_seconds``."""
    frames_dir = workdir / "captions"
    frames_dir.mkdir(parents=True, exist_ok=True)
    blank = frames_dir / "blank.png"
    Image.new("RGBA", size, (0, 0, 0, 0)).save(blank)
    entries: list[tuple[Path, float]] = []
    cursor = 0.0
    for li, line in enumerate(lines):
        if line[0].start > cursor:
            entries.append((blank, line[0].start - cursor))
        for wi, word in enumerate(line):
            end = line[wi + 1].start if wi + 1 < len(line) else word.end
            image = _caption_image(line, wi, size, brand, frames_dir / f"l{li:03d}w{wi:02d}.png")
            entries.append((image, max(1 / FPS, end - word.start)))
        cursor = line[-1].end
    if total_seconds > cursor:
        entries.append((blank, total_seconds - cursor))
    listing = workdir / "captions.txt"
    body = "".join(f"file '{p.as_posix()}'\nduration {d:.3f}\n" for p, d in entries)
    # The concat demuxer ignores the last duration unless the file is repeated.
    listing.write_text(body + f"file '{entries[-1][0].as_posix()}'\n")
    track = workdir / "captions.mov"
    run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", f"fps={FPS},format=rgba", "-c:v", "png",
            "-t", f"{total_seconds:.3f}", str(track),
        ]
    )  # fmt: skip
    return track
