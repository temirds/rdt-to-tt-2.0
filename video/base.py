from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoSegment:
    text: str
    pause_after_seconds: float = 0.0
    role: str = "answer"


@dataclass(frozen=True)
class VideoScene:
    text: str
    role: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class VideoOptions:
    audio_path: Path
    output_path: Path | None = None
    output_dir: Path | None = None
    filename: str | None = None
    duration_seconds: float | None = None
    background_path: Path | None = None
    background_tags: tuple[str, ...] = ()
    config_path: Path | None = None
    auto_fetch_background: bool = True


@dataclass(frozen=True)
class VideoResult:
    output_path: Path
    background_path: Path
    subtitles_path: Path
    duration_seconds: float
    scene_count: int
