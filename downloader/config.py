from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_ROOT.parent
DEFAULT_CONFIG_PATH = MODULE_ROOT / "configs" / "downloader.json"


@dataclass(frozen=True)
class DownloaderConfig:
    config_path: Path = DEFAULT_CONFIG_PATH
    default_provider: str = "youtube"
    default_output_dir: Path = PROJECT_ROOT / "out" / "youtube"
    filename_template: str = "%(title).180B [%(id)s].%(ext)s"
    default_quality: str = "best"
    with_audio: bool = False
    prefer_mp4: bool = True
    no_playlist: bool = True
    retries: int = 10
    fragment_retries: int = 10
    socket_timeout: int = 30
    cookies_path: Path | None = None
    ffmpeg_location: Path | None = None


@dataclass(frozen=True)
class LoadedDownloaderSettings:
    config: DownloaderConfig
    path: Path


def load_downloader_settings(path: Path = DEFAULT_CONFIG_PATH) -> LoadedDownloaderSettings:
    path = path.resolve()
    raw = _load_json(path)
    downloader_raw = raw.get("downloader") or raw.get("youtube_downloader", {})

    config = DownloaderConfig(
        config_path=path,
        default_provider=str(downloader_raw.get("default_provider", DownloaderConfig.default_provider)),
        default_output_dir=_resolve_path(
            downloader_raw.get("default_output_dir", "../out/youtube"),
            path.parent.parent,
        ),
        filename_template=str(
            downloader_raw.get("filename_template", DownloaderConfig.filename_template)
        ),
        default_quality=str(downloader_raw.get("default_quality", DownloaderConfig.default_quality)),
        with_audio=bool(downloader_raw.get("with_audio", DownloaderConfig.with_audio)),
        prefer_mp4=bool(downloader_raw.get("prefer_mp4", DownloaderConfig.prefer_mp4)),
        no_playlist=bool(downloader_raw.get("no_playlist", DownloaderConfig.no_playlist)),
        retries=int(downloader_raw.get("retries", DownloaderConfig.retries)),
        fragment_retries=int(
            downloader_raw.get("fragment_retries", DownloaderConfig.fragment_retries)
        ),
        socket_timeout=int(downloader_raw.get("socket_timeout", DownloaderConfig.socket_timeout)),
        cookies_path=_resolve_optional_path(downloader_raw.get("cookies_path"), path.parent.parent),
        ffmpeg_location=_resolve_optional_path(downloader_raw.get("ffmpeg_location"), path.parent.parent),
    )
    return LoadedDownloaderSettings(config=config, path=path)


YoutubeDownloaderConfig = DownloaderConfig
LoadedYoutubeDownloaderSettings = LoadedDownloaderSettings
load_youtube_downloader_settings = load_downloader_settings


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Downloader config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def _resolve_optional_path(value: str | Path | None, base_dir: Path) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return _resolve_path(value, base_dir)
