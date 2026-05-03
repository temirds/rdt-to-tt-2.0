from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .assets import cleanup_temporary_backgrounds, log_video, resolve_backgrounds
from .base import VideoOptions, VideoResult, VideoSegment
from .config import DEFAULT_CONFIG_PATH, load_video_settings
from .renderer import build_background_reel, probe_audio_duration, render_video
from .subtitles import write_ass_subtitles
from .timeline import build_scenes


def generate_video(
    segments: Sequence[VideoSegment],
    options: VideoOptions | Mapping[str, Any],
    **overrides: Any,
) -> VideoResult:
    opts = _build_options(options, overrides)
    settings = load_video_settings(opts.config_path or DEFAULT_CONFIG_PATH)
    config = settings.config

    audio_path = Path(opts.audio_path).resolve()
    if not audio_path.exists():
        raise SystemExit(f"Audio file not found: {audio_path}")

    duration_seconds = opts.duration_seconds
    if duration_seconds is None:
        log_video("Видео: измеряем длительность аудио через ffprobe")
        duration_seconds = probe_audio_duration(config, audio_path)
    duration_seconds = float(duration_seconds)
    log_video(f"Видео: длительность {duration_seconds:.2f} сек, размер {config.width}x{config.height}, fps={config.fps}")

    output_path = _resolve_output_path(config.default_output_dir, opts)
    background_paths = resolve_backgrounds(config, opts.background_path, opts.auto_fetch_background, tags=opts.background_tags)
    background_path = background_paths[0]
    temporary_paths = tuple(background_paths)
    try:
        if len(background_paths) > 1:
            reel_path = config.cache_dir / "tmp" / "background_reels" / f"{output_path.stem}_background.mp4"
            log_video(f"Видео: собираем фоновой reel из {len(background_paths)} клипов")
            background_path = build_background_reel(config, background_paths, reel_path, duration_seconds)
            temporary_paths = (*temporary_paths, background_path)
        scenes = build_scenes(tuple(segments), duration_seconds)
        log_video(f"Видео: сцен субтитров {len(scenes)}")
        subtitles_path = output_path.with_suffix(".ass")
        log_video(f"Видео: пишем субтитры {subtitles_path}")
        write_ass_subtitles(subtitles_path, scenes, config)
        log_video(f"Видео: запускаем ffmpeg render -> {output_path}")
        render_video(
            config=config,
            background_path=background_path,
            audio_path=audio_path,
            subtitles_path=subtitles_path,
            output_path=output_path,
            duration_seconds=duration_seconds,
        )
        log_video(f"Видео: файл готов {output_path}")
        return VideoResult(
            output_path=output_path,
            background_path=background_path,
            subtitles_path=subtitles_path,
            duration_seconds=duration_seconds,
            scene_count=len(scenes),
        )
    finally:
        cleanup_temporary_backgrounds(config, temporary_paths)


def _build_options(options: VideoOptions | Mapping[str, Any], overrides: dict[str, Any]) -> VideoOptions:
    if isinstance(options, VideoOptions):
        values = {
            "audio_path": options.audio_path,
            "output_path": options.output_path,
            "output_dir": options.output_dir,
            "filename": options.filename,
            "duration_seconds": options.duration_seconds,
            "background_path": options.background_path,
            "background_tags": options.background_tags,
            "config_path": options.config_path,
            "auto_fetch_background": options.auto_fetch_background,
        }
    else:
        values = dict(options)
    values.update({key: value for key, value in overrides.items() if value is not None})
    return VideoOptions(**values)


def _resolve_output_path(default_output_dir: Path, options: VideoOptions) -> Path:
    if options.output_path is not None:
        output_path = Path(options.output_path)
    else:
        output_dir = Path(options.output_dir) if options.output_dir is not None else default_output_dir
        filename = options.filename or f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        output_path = output_dir / filename
    if output_path.suffix.lower() != ".mp4":
        output_path = output_path.with_suffix(".mp4")
    return output_path.resolve()
