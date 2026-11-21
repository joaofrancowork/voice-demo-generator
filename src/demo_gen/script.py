"""The demo script: what is said in each scene and what is shown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Aspect = Literal["16:9", "9:16"]


class Scene(BaseModel):
    narration: str = Field(min_length=1, max_length=1500)
    title: str | None = Field(default=None, description="Render a title card with this text")
    subtitle: str | None = None
    image: Path | None = None
    video: Path | None = Field(
        default=None, description="Screen recording; looped or trimmed to fit"
    )
    pause_after: float = Field(default=0.4, ge=0, le=5)

    @model_validator(mode="after")
    def one_visual(self) -> Scene:
        visuals = [v for v in (self.title, self.image, self.video) if v is not None]
        if len(visuals) != 1:
            raise ValueError("each scene needs exactly one of: title, image, video")
        return self


class Voice(BaseModel):
    provider: Literal["elevenlabs", "say", "tone"] = "say"
    voice: str = Field(default="Samantha", description="ElevenLabs voice_id or macOS voice name")
    model: str = "eleven_multilingual_v2"
    consent: bool = Field(
        default=False,
        description="Required for cloned voices: the speaker agreed to this use of their voice",
    )
    cloned: bool = False

    @model_validator(mode="after")
    def cloned_needs_consent(self) -> Voice:
        if self.cloned and not self.consent:
            raise ValueError(
                "cloned voices require consent: true (documented permission from the speaker)"
            )
        return self


class Brand(BaseModel):
    background: str = "#0b0b0b"
    foreground: str = "#ffffff"
    accent: str = "#d6ff3d"
    font: Path | None = None


class DemoScript(BaseModel):
    name: str
    language: str = "en"
    pronunciations: dict[str, str] = Field(
        default_factory=dict,
        description="Spoken form of product names and acronyms, e.g. {'ENT-42': 'E N T 42'}",
    )
    voice: Voice = Voice()
    brand: Brand = Brand()
    scenes: list[Scene] = Field(min_length=1)

    def spoken(self, text: str) -> str:
        """The text as it should be spoken: whole-word replacements, longest terms first."""
        for term in sorted(self.pronunciations, key=len, reverse=True):
            pattern = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
            text = re.sub(pattern, self.pronunciations[term], text)
        return text

    @classmethod
    def load(cls, path: Path) -> DemoScript:
        script = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        base = path.parent
        for scene in script.scenes:
            for attr in ("image", "video"):
                value = getattr(scene, attr)
                if value is not None and not value.is_absolute():
                    setattr(scene, attr, (base / value).resolve())
        if script.brand.font is not None and not script.brand.font.is_absolute():
            script.brand.font = (base / script.brand.font).resolve()
        return script
