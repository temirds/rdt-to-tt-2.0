from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    audio_path: Path
    prompt_text: str


@dataclass(frozen=True)
class VoiceoverRequest:
    text: str
    voice: VoiceProfile
    output_path: Path
    pitch: float = 0.0
    speed: float = 1.0


@dataclass(frozen=True)
class VoiceoverResult:
    output_path: Path
    duration_seconds: float
    sample_rate: int


class VoiceoverEngine(Protocol):
    def synthesize_to_file(self, request: VoiceoverRequest) -> VoiceoverResult:
        ...
