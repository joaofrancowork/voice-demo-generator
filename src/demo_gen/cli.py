"""demo-gen render script.yaml [--aspect 16:9|9:16|both] [--voice-provider ...]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from demo_gen.captions import Transcriber
from demo_gen.pipeline import MAX_WER, render
from demo_gen.script import DemoScript
from demo_gen.tts import build_tts


def main(argv: list[str] | None = None, transcriber: Transcriber | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo-gen")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="Render a script into a narrated video")
    r.add_argument("script", type=Path)
    r.add_argument("--out", type=Path, default=Path("out"))
    r.add_argument("--aspect", choices=["16:9", "9:16", "both"], default="16:9")
    r.add_argument("--voice-provider", choices=["elevenlabs", "say", "tone"])
    r.add_argument("--whisper-model", default="small")
    r.add_argument("--strict", action="store_true", help=f"Fail if any scene WER > {MAX_WER:.0%}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    script = DemoScript.load(args.script)
    if args.voice_provider:
        script.voice.provider = args.voice_provider
    tts = build_tts(script.voice)
    if transcriber is None:  # pragma: no cover
        from demo_gen.captions import WhisperTranscriber

        transcriber = WhisperTranscriber(args.whisper_model)
    args.out.mkdir(parents=True, exist_ok=True)
    aspects = ["16:9", "9:16"] if args.aspect == "both" else [args.aspect]
    failed = False
    for aspect in aspects:
        suffix = "landscape" if aspect == "16:9" else "vertical"
        out = args.out / f"{script.name}-{suffix}.mp4"
        report = render(script, tts, transcriber, out, aspect)  # type: ignore[arg-type]
        print(f"{out} · {report.seconds}s · {len(report.scenes)} scenes")
        for scene in report.flagged:
            print(f"  scene {scene.index}: WER {scene.wer:.0%} — heard: {scene.transcript!r}")
        failed = failed or bool(report.flagged)
    return 1 if failed and args.strict else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
