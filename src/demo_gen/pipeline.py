"""Script -> narration -> alignment -> scenes -> final video."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from demo_gen import ffmpeg
from demo_gen.captions import Transcriber, Word, chunk, to_ass, to_srt, word_error_rate
from demo_gen.cards import title_card
from demo_gen.overlay import caption_track
from demo_gen.script import Aspect, DemoScript
from demo_gen.tts import TTS

log = logging.getLogger(__name__)
MAX_WER = 0.15


@dataclass
class SceneReport:
    index: int
    seconds: float
    transcript: str
    wer: float


@dataclass
class RenderReport:
    output: str
    aspect: str
    seconds: float
    scenes: list[SceneReport] = field(default_factory=list)

    @property
    def flagged(self) -> list[SceneReport]:
        return [s for s in self.scenes if s.wer > MAX_WER]


def render(
    script: DemoScript,
    tts: TTS,
    transcriber: Transcriber,
    out: Path,
    aspect: Aspect = "16:9",
    workdir: Path | None = None,
) -> RenderReport:
    size = ffmpeg.SIZES[aspect]
    # ffmpeg's concat lists resolve relative paths against the list file: use absolute ones.
    out = out.resolve()
    work = (workdir or out.parent / f".build-{out.stem}").resolve()
    work.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    all_words: list[Word] = []
    report = RenderReport(output=str(out), aspect=aspect, seconds=0.0)
    offset = 0.0

    for i, scene in enumerate(script.scenes, 1):
        to_say = script.spoken(scene.narration)
        audio = tts.synthesize(to_say, work / f"scene{i:02d}.wav")
        words = transcriber.words(audio, script.language)
        spoken = " ".join(w.text for w in words)
        # Compare with what was meant to be *said*, so a pronunciation entry never
        # counts as an error.
        wer = word_error_rate(to_say, spoken)
        if wer > MAX_WER:
            log.warning("scene %d: narration differs from the script (WER %.0f%%)", i, wer * 100)
        seconds = ffmpeg.duration(audio) + scene.pause_after
        if scene.title is not None:
            visual = title_card(
                scene.title, scene.subtitle, size, script.brand, work / f"scene{i:02d}.png"
            )
            is_video = False
        else:
            visual = scene.video or scene.image  # type: ignore[assignment]
            assert visual is not None
            is_video = scene.video is not None
        clip = work / f"scene{i:02d}.mp4"
        ffmpeg.run(ffmpeg.scene_clip(visual, audio, clip, seconds, size, is_video))
        clips.append(clip)
        all_words += [Word(w.text, w.start + offset, w.end + offset) for w in words]
        report.scenes.append(SceneReport(i, round(seconds, 2), spoken, round(wer, 3)))
        offset += seconds

    lines = chunk(all_words)
    # Sidecar subtitles for editors and platforms that take a caption file.
    out.with_suffix(".srt").write_text(to_srt(lines), encoding="utf-8")
    out.with_suffix(".ass").write_text(to_ass(lines, aspect, script.brand), encoding="utf-8")
    joined = work / "joined.mp4"
    ffmpeg.run(ffmpeg.concat(clips, joined, work))
    captions = caption_track(lines, offset, size, script.brand, work)
    ffmpeg.run(ffmpeg.finish(joined, captions, out))
    report.seconds = round(ffmpeg.duration(out), 2)
    out.with_suffix(".report.json").write_text(json.dumps(asdict(report), indent=2))
    return report
