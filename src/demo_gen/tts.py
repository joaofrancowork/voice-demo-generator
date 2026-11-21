"""Text-to-speech providers behind one interface.

* ``elevenlabs``: production quality, any voice in the account, including an
  Instant Voice Clone (the script must declare consent for cloned voices).
* ``say``: macOS built-in voices, offline and free, good for drafts.
* ``tone``: a silent-ish placeholder whose length follows the text; used in tests
  and CI where no TTS is available.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol

import httpx

from demo_gen.ffmpeg import run
from demo_gen.script import Voice

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class TTSError(RuntimeError):
    pass


class TTS(Protocol):
    def synthesize(self, text: str, out: Path) -> Path:
        """Write speech for ``text`` to ``out`` (WAV, 44.1 kHz mono) and return it."""
        ...


def to_wav(source: Path, out: Path) -> Path:
    run(["ffmpeg", "-y", "-i", str(source), "-ar", "44100", "-ac", "1", str(out)])
    return out


class ElevenLabsTTS:
    def __init__(self, voice: Voice, client: httpx.Client | None = None) -> None:
        key = os.getenv("ELEVENLABS_API_KEY")
        if not key and client is None:
            raise TTSError("Set ELEVENLABS_API_KEY to use the elevenlabs provider")
        self.voice = voice
        self.client = client or httpx.Client(timeout=120, headers={"xi-api-key": key or ""})

    def synthesize(self, text: str, out: Path) -> Path:
        response = self.client.post(
            ELEVENLABS_URL.format(voice_id=self.voice.voice),
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": self.voice.model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
        if response.status_code != 200:
            raise TTSError(f"ElevenLabs returned {response.status_code}: {response.text[:200]}")
        mp3 = out.with_suffix(".mp3")
        mp3.write_bytes(response.content)
        return to_wav(mp3, out)


class SayTTS:
    def __init__(self, voice: Voice) -> None:
        if shutil.which("say") is None:
            raise TTSError("The 'say' provider needs macOS")
        self.voice = voice

    def synthesize(self, text: str, out: Path) -> Path:  # pragma: no cover - macOS only
        aiff = out.with_suffix(".aiff")
        run(["say", "-v", self.voice.voice, "-o", str(aiff), text])
        return to_wav(aiff, out)


class ToneTTS:
    """Deterministic stand-in: 0.33 s per word of quiet tone."""

    seconds_per_word = 0.33

    def synthesize(self, text: str, out: Path) -> Path:
        seconds = max(1.0, len(text.split()) * self.seconds_per_word)
        run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds:.2f}",
                "-af", "volume=0.05", "-ar", "44100", "-ac", "1", str(out),
            ]
        )  # fmt: skip
        return out


def build_tts(voice: Voice) -> TTS:
    if voice.provider == "elevenlabs":
        return ElevenLabsTTS(voice)
    if voice.provider == "say":
        return SayTTS(voice)
    return ToneTTS()
