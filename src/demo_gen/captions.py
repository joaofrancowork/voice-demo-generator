"""Whisper alignment, caption building (ASS) and the script check.

Captions are built from Whisper *word timestamps of the generated audio*, not
estimated from text length, so they stay in sync whatever the voice's pace.
The same transcription is compared with the script (word error rate) to catch
TTS failures: skipped sentences, mispronounced product names, garbled numbers.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from demo_gen.script import Aspect, Brand


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


class Transcriber(Protocol):
    def words(self, audio: Path, language: str) -> list[Word]: ...


def pcm16k(audio: Path) -> npt.NDArray[np.float32]:
    """Decode with ffmpeg to 16 kHz mono float32, the input Whisper expects.

    Doing it here avoids faster-whisper's PyAV decoder, whose API changes between
    releases, and reuses the ffmpeg that the rest of the pipeline already needs.
    """
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


class WhisperTranscriber:  # pragma: no cover - downloads a model; exercised end to end
    def __init__(self, model: str = "small") -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model, device="cpu", compute_type="int8")

    def words(self, audio: Path, language: str) -> list[Word]:
        segments, _ = self.model.transcribe(pcm16k(audio), language=language, word_timestamps=True)
        return [
            Word(w.word.strip(), w.start, w.end)
            for segment in segments
            for w in (segment.words or [])
            if w.word.strip()
        ]


_TOKEN = re.compile(r"[\w']+")


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = tokens(reference), tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h))
        previous = current
    return previous[-1] / len(ref)


def chunk(words: list[Word], max_words: int = 5, max_gap: float = 0.6) -> list[list[Word]]:
    """Short caption lines that break on pauses and punctuation, like social-video captions."""
    lines: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current and (
            len(current) >= max_words
            or word.start - current[-1].end > max_gap
            or current[-1].text.endswith((".", "?", "!", ","))
        ):
            lines.append(current)
            current = []
        current.append(word)
    if current:
        lines.append(current)
    return lines


def _ts(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    h, rem = divmod(centis, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_colour(hex_colour: str) -> str:
    r, g, b = hex_colour[1:3], hex_colour[3:5], hex_colour[5:7]
    return f"&H00{b}{g}{r}".upper()


def to_ass(lines: Iterable[list[Word]], aspect: Aspect, brand: Brand) -> str:
    """ASS subtitles: the spoken word is highlighted in the accent colour (karaoke style)."""
    width, height = (1920, 1080) if aspect == "16:9" else (1080, 1920)
    size = 64 if aspect == "16:9" else 76
    margin = 90 if aspect == "16:9" else 420
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Helvetica,{size},{_ass_colour(brand.accent)},{_ass_colour(brand.foreground)},&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,0,2,80,80,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""  # noqa: E501
    events = []
    for line in lines:
        karaoke = "".join(
            f"{{\\k{max(1, round((w.end - w.start) * 100))}}}{w.text} " for w in line
        ).strip()
        events.append(
            f"Dialogue: 0,{_ts(line[0].start)},{_ts(line[-1].end)},Caption,,0,0,0,,{karaoke}"
        )
    return header + "\n".join(events) + "\n"


def to_srt(lines: Iterable[list[Word]]) -> str:
    def stamp(t: float) -> str:
        ms = round(t * 1000)
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = [
        f"{i}\n{stamp(line[0].start)} --> {stamp(line[-1].end)}\n{' '.join(w.text for w in line)}\n"
        for i, line in enumerate(lines, 1)
    ]
    return "\n".join(blocks)
