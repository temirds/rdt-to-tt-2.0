from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = MODULE_ROOT / "configs" / "video.json"


@dataclass(frozen=True)
class VideoConfig:
    config_path: Path = DEFAULT_CONFIG_PATH
    env_path: Path = MODULE_ROOT / ".env"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 20
    preset: str = "veryfast"
    backgrounds_dir: Path = MODULE_ROOT / "assets" / "backgrounds"
    cache_dir: Path = MODULE_ROOT / "cache"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    default_output_dir: Path = Path("out")
    background_urls: tuple[str, ...] = ()
    background_url_groups: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "minecraft": (),
            "subway": (),
            "satisfying": (),
            "driving": (),
            "cooking": (),
            "nature": (),
            "city": (),
            "fitness": (),
        }
    )
    background_default_tags: tuple[str, ...] = ("satisfying", "driving", "city", "nature")
    min_background_seconds: float = 8.0
    font_name: str = "Arial"
    question_font_size: int = 58
    answer_font_size: int = 64
    text_margin_v: int = 260
    text_margin_h: int = 90


@dataclass(frozen=True)
class LoadedVideoSettings:
    config: VideoConfig
    path: Path


def load_video_settings(path: Path = DEFAULT_CONFIG_PATH) -> LoadedVideoSettings:
    path = path.resolve()
    raw = _load_json(path)
    video_raw = raw.get("video", {})
    backgrounds_raw = raw.get("backgrounds", {})
    style_raw = raw.get("style", {})

    config = VideoConfig(
        config_path=path,
        env_path=_resolve_path(video_raw.get("env_path", ".env"), path.parent.parent),
        width=int(video_raw.get("width", VideoConfig.width)),
        height=int(video_raw.get("height", VideoConfig.height)),
        fps=int(video_raw.get("fps", VideoConfig.fps)),
        crf=int(video_raw.get("crf", VideoConfig.crf)),
        preset=str(video_raw.get("preset", VideoConfig.preset)),
        backgrounds_dir=_resolve_path(video_raw.get("backgrounds_dir", "assets/backgrounds"), path.parent.parent),
        cache_dir=_resolve_path(video_raw.get("cache_dir", "cache"), path.parent.parent),
        ffmpeg_path=str(video_raw.get("ffmpeg_path", VideoConfig.ffmpeg_path)),
        ffprobe_path=str(video_raw.get("ffprobe_path", VideoConfig.ffprobe_path)),
        default_output_dir=_resolve_path(video_raw.get("default_output_dir", "../out"), path.parent.parent),
        background_urls=_to_tuple(backgrounds_raw.get("urls", VideoConfig().background_urls)),
        background_url_groups=_load_tag_queries(backgrounds_raw.get("tags", VideoConfig().background_url_groups)),
        background_default_tags=_to_tuple(backgrounds_raw.get("default_tags", VideoConfig().background_default_tags)),
        min_background_seconds=float(video_raw.get("min_background_seconds", VideoConfig.min_background_seconds)),
        font_name=str(style_raw.get("font_name", VideoConfig.font_name)),
        question_font_size=int(style_raw.get("question_font_size", VideoConfig.question_font_size)),
        answer_font_size=int(style_raw.get("answer_font_size", VideoConfig.answer_font_size)),
        text_margin_v=int(style_raw.get("text_margin_v", VideoConfig.text_margin_v)),
        text_margin_h=int(style_raw.get("text_margin_h", VideoConfig.text_margin_h)),
    )
    return LoadedVideoSettings(config=config, path=path)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Video config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _to_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (list, tuple)):
        return tuple(str(value).strip() for value in values if str(value).strip())
    return (str(values).strip(),) if str(values).strip() else ()


def _load_tag_queries(values: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(values, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for raw_tag, raw_queries in values.items():
        tag = _normalize_tag(str(raw_tag))
        queries = _to_tuple(raw_queries)
        if tag:
            result[tag] = queries
    return result


def _normalize_tag(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")
