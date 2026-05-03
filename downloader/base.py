from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DownloadOptions:
    provider: str = "youtube"
    config_path: Path | str | None = None
    output_dir: Path | str | None = None
    filename_template: str | None = None
    quality: str | None = None
    format_selector: str | None = None
    with_audio: bool | None = None
    prefer_mp4: bool | None = None
    cookies_path: Path | str | None = None
    no_playlist: bool | None = None
    retries: int | None = None
    fragment_retries: int | None = None
    socket_timeout: int | None = None
    ffmpeg_location: Path | str | None = None


@dataclass(frozen=True)
class DownloadResult:
    url: str
    success: bool
    filepaths: tuple[Path, ...]
    provider: str = "youtube"
    title: str | None = None
    video_id: str | None = None
    error: str | None = None
    info: dict[str, Any] | None = None


YoutubeDownloadOptions = DownloadOptions
YoutubeDownloadResult = DownloadResult
