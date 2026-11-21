from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image

from demo_gen import cli
from demo_gen.captions import Word, chunk, pcm16k, to_ass, to_srt, tokens, word_error_rate
from demo_gen.cards import title_card
from demo_gen.ffmpeg import FFmpegError, duration, run
from demo_gen.pipeline import render
from demo_gen.script import Brand, DemoScript, Scene, Voice
from demo_gen.tts import ElevenLabsTTS, ToneTTS, TTSError, build_tts


class ScriptTranscriber:
    """Pretends the audio says the scene's narration, evenly spaced over its length."""

    def __init__(self, texts: list[str], garble_scene: int | None = None) -> None:
        self.texts = texts
        self.calls = 0
        self.garble_scene = garble_scene

    def words(self, audio: Path, language: str) -> list[Word]:
        text = self.texts[self.calls]
        self.calls += 1
        if self.calls == self.garble_scene:
            text = "completely different words were spoken here"
        parts = text.split()
        step = duration(audio) / len(parts)
        return [Word(w, i * step, (i + 1) * step) for i, w in enumerate(parts)]


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    clip = tmp_path / "screen.mp4"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1",
         "-pix_fmt", "yuv420p", str(clip)])  # fmt: skip
    Image.new("RGB", (800, 1200), "#336699").save(tmp_path / "phone.png")
    return tmp_path


def _script(assets: Path) -> DemoScript:
    return DemoScript(
        name="demo",
        voice=Voice(provider="tone"),
        scenes=[
            Scene(
                narration="Meet Northwind, bookings on autopilot.",
                title="Northwind",
                subtitle="Demo",
            ),
            Scene(
                narration="Here is the dashboard your team sees every morning.",
                video=assets / "screen.mp4",
            ),
            Scene(
                narration="Customers book from their phone in seconds.", image=assets / "phone.png"
            ),
        ],
    )


def test_render_both_aspects_end_to_end(assets: Path, tmp_path: Path) -> None:
    script = _script(assets)
    texts = [s.narration for s in script.scenes]
    for aspect, size in (("16:9", (1920, 1080)), ("9:16", (1080, 1920))):
        out = tmp_path / f"out-{aspect.replace(':', 'x')}.mp4"
        report = render(script, ToneTTS(), ScriptTranscriber(texts), out, aspect)  # type: ignore[arg-type]
        probe = json.loads(
            subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height", "-of", "json", str(out)],
                capture_output=True, text=True, check=True,
            ).stdout
        )  # fmt: skip
        assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == size
        expected = sum(s.seconds for s in report.scenes)
        assert report.seconds == pytest.approx(expected, abs=0.25)
        assert report.flagged == []
        assert "-->" in out.with_suffix(".srt").read_text()
        assert json.loads(out.with_suffix(".report.json").read_text())["aspect"] == aspect


def test_script_check_flags_a_scene_that_was_not_said(assets: Path, tmp_path: Path) -> None:
    script = _script(assets)
    transcriber = ScriptTranscriber([s.narration for s in script.scenes], garble_scene=2)
    report = render(script, ToneTTS(), transcriber, tmp_path / "x.mp4")
    assert [s.index for s in report.flagged] == [2]


def test_cli_strict_mode_and_both_aspects(
    assets: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml_path = assets / "demo.yaml"
    yaml_path.write_text(
        "name: cli-demo\nvoice: {provider: say}\nscenes:\n"
        "  - {narration: 'Hello there.', title: 'Hello'}\n"
        "  - {narration: 'A phone screen.', image: phone.png}\n"
    )
    texts = ["Hello there.", "A phone screen."]
    ok = cli.main(
        [
            "render",
            str(yaml_path),
            "--out",
            str(tmp_path / "o"),
            "--aspect",
            "both",
            "--voice-provider",
            "tone",
        ],
        transcriber=ScriptTranscriber(texts * 2),
    )
    assert ok == 0
    out = capsys.readouterr().out
    assert "cli-demo-landscape.mp4" in out and "cli-demo-vertical.mp4" in out
    failing = cli.main(
        [
            "render",
            str(yaml_path),
            "--out",
            str(tmp_path / "p"),
            "--voice-provider",
            "tone",
            "--strict",
        ],
        transcriber=ScriptTranscriber(texts, garble_scene=1),
    )
    assert failing == 1 and "WER" in capsys.readouterr().out


def test_script_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Scene(narration="x", title="a", image=Path("b.png"))
    with pytest.raises(ValueError, match="consent"):
        Voice(provider="elevenlabs", voice="abc", cloned=True)
    assert Voice(provider="elevenlabs", voice="abc", cloned=True, consent=True).cloned
    path = tmp_path / "s.yaml"
    path.write_text(
        "name: n\nbrand: {font: fonts/x.ttf}\nscenes: [{narration: hi, video: clips/a.mp4}]\n"
    )
    script = DemoScript.load(path)
    assert script.scenes[0].video == (tmp_path / "clips/a.mp4").resolve()
    assert script.brand.font == (tmp_path / "fonts/x.ttf").resolve()


def test_word_error_rate_and_tokens() -> None:
    assert tokens("It's 9:30, OK?") == ["it's", "9", "30", "ok"]
    assert word_error_rate("book a table", "book a table") == 0.0
    assert word_error_rate("book a table", "book the table") == pytest.approx(1 / 3)
    assert word_error_rate("book a table", "book") == pytest.approx(2 / 3)
    assert word_error_rate("", "") == 0.0 and word_error_rate("", "x") == 1.0


def test_chunking_breaks_on_size_pauses_and_punctuation() -> None:
    words = [
        Word(w, i * 0.3, i * 0.3 + 0.25)
        for i, w in enumerate(["one", "two", "three", "four", "five", "six"])
    ]
    assert [len(line) for line in chunk(words, max_words=4)] == [4, 2]
    paused = [Word("a", 0, 0.2), Word("b", 2.0, 2.2)]
    assert len(chunk(paused)) == 2
    sentence = [Word("Hi.", 0, 0.2), Word("Next", 0.3, 0.5)]
    assert len(chunk(sentence)) == 2


def test_subtitle_formats() -> None:
    lines = [[Word("Hello", 0.0, 0.5), Word("world", 0.5, 1.25)], [Word("again", 3661.5, 3662.0)]]
    ass = to_ass(lines, "9:16", Brand(accent="#d6ff3d"))
    assert "PlayResX: 1080" in ass and "&H003DFFD6" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.25,Caption,,0,0,0,,{\\k50}Hello {\\k75}world" in ass
    assert "1:01:01.50" in ass
    srt = to_srt(lines)
    assert "00:00:00,000 --> 00:00:01,250\nHello world" in srt and "01:01:01,500" in srt


def test_title_card_renders_in_brand_colours(tmp_path: Path) -> None:
    out = title_card(
        "A long title that needs wrapping onto lines",
        "sub",
        (1080, 1920),
        Brand(),
        tmp_path / "c.png",
    )
    image = Image.open(out)
    assert image.size == (1080, 1920) and image.getpixel((5, 5)) == (11, 11, 11)


def test_elevenlabs_request_and_errors(tmp_path: Path) -> None:
    mp3 = tmp_path / "tone.mp3"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=duration=0.5", str(mp3)])
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        if "bad" in str(request.url):
            return httpx.Response(401, text="invalid api key")
        return httpx.Response(200, content=mp3.read_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTTS(Voice(provider="elevenlabs", voice="voice123"), client=client)
    wav = tts.synthesize("Hello", tmp_path / "out.wav")
    assert duration(wav) == pytest.approx(0.5, abs=0.05)
    assert "voice123" in str(seen["url"]) and seen["body"]["model_id"] == "eleven_multilingual_v2"  # type: ignore[index]
    bad = ElevenLabsTTS(Voice(provider="elevenlabs", voice="bad"), client=client)
    with pytest.raises(TTSError, match="401"):
        bad.synthesize("x", tmp_path / "bad.wav")


def test_build_tts_and_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY"):
        build_tts(Voice(provider="elevenlabs"))
    assert isinstance(build_tts(Voice(provider="tone")), ToneTTS)


def test_ffmpeg_errors_are_readable(tmp_path: Path) -> None:
    with pytest.raises(FFmpegError, match="ffprobe failed"):
        duration(tmp_path / "missing.wav")


def test_pcm16k_decodes_to_whisper_input(tmp_path: Path) -> None:
    wav = ToneTTS().synthesize("one two three four five six", tmp_path / "t.wav")
    samples = pcm16k(wav)
    assert samples.dtype.name == "float32"
    assert len(samples) / 16000 == pytest.approx(duration(wav), abs=0.05)


def test_relative_output_directory(assets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(assets)
    script = _script(assets)
    script.scenes = script.scenes[:1]
    report = render(
        script, ToneTTS(), ScriptTranscriber([script.scenes[0].narration]), Path("rel/out.mp4")
    )
    assert (assets / "rel" / "out.mp4").exists() and report.seconds > 0


def test_cli_help_renders(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["render", "--help"])
    assert "WER > 15%" in capsys.readouterr().out
