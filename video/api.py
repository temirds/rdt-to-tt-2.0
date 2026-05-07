from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .assets import cleanup_temporary_backgrounds, log_video, resolve_backgrounds
from .base import VideoOptions, VideoResult, VideoSegment
from .config import DEFAULT_CONFIG_PATH, load_video_settings
from .renderer import extract_background_segment, mix_final_audio, probe_audio_duration, probe_video_duration, render_video
from .subtitles import write_drawtext_subtitle_filter
from .usage import BackgroundUsageDatabase


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

    voice_duration_seconds = opts.duration_seconds
    if voice_duration_seconds is None:
        log_video("Видео: измеряем длительность аудио через ffprobe")
        voice_duration_seconds = probe_audio_duration(config, audio_path)
    voice_duration_seconds = float(voice_duration_seconds)
    render_duration_seconds = voice_duration_seconds + max(0.0, config.tail_seconds)
    log_video(
        f"Видео: озвучка {voice_duration_seconds:.2f} сек, итог {render_duration_seconds:.2f} сек, "
        f"размер {config.width}x{config.height}, fps={config.fps}"
    )

    output_path = _resolve_output_path(config.default_output_dir, opts)
    prepared_segments = tuple(segments)
    mixed_audio_path = config.cache_dir / "tmp" / "audio" / f"{output_path.stem}_mix.m4a"
    log_video("Аудио: миксуем озвучку и музыку")
    mixed_audio_path, music_path, _ = mix_final_audio(
        config=config,
        voice_path=audio_path,
        output_path=mixed_audio_path,
        duration_seconds=render_duration_seconds,
        sfx_times=(),
    )
    if music_path:
        log_video(f"Аудио: музыка {music_path.name}, громкость {config.music_volume}")

    background_paths = resolve_backgrounds(
        config,
        opts.background_path,
        opts.auto_fetch_background,
        target_count=0,
        tags=opts.background_tags,
    )
    usage_db_path = config.background_usage_db_path or config.cache_dir / "background_usage.sqlite3"
    usage_db = BackgroundUsageDatabase(usage_db_path)
    selection = usage_db.select_segment(
        background_paths,
        render_duration_seconds,
        lambda path: probe_video_duration(config, path),
    )
    log_video(
        "Фон: выбран "
        f"{selection.path.name} с {selection.start_seconds:.2f} до "
        f"{selection.used_until_seconds:.2f} из {selection.source_duration_seconds:.2f} сек"
    )
    if selection.exhausted:
        log_video("Фон: файл после этого ролика помечен как полностью использованный")

    background_path = config.cache_dir / "tmp" / "background_segments" / f"{output_path.stem}_background.mp4"
    temporary_paths = (background_path, mixed_audio_path)
    try:
        log_video("Видео: вырезаем нужный диапазон фонового видео")
        extract_background_segment(
            config=config,
            background_path=selection.path,
            output_path=background_path,
            start_seconds=selection.start_seconds,
            duration_seconds=render_duration_seconds,
        )
        subtitles_path = output_path.with_suffix(".filter")
        log_video(f"Видео: пишем drawtext субтитры {subtitles_path}")
        write_drawtext_subtitle_filter(
            subtitles_path,
            prepared_segments,
            audio_path=audio_path,
            duration_seconds=voice_duration_seconds,
            config=config,
        )
        log_video(f"Видео: запускаем ffmpeg render -> {output_path}")
        render_video(
            config=config,
            background_path=background_path,
            audio_path=mixed_audio_path,
            subtitles_path=subtitles_path,
            output_path=output_path,
            duration_seconds=render_duration_seconds,
        )
        log_video(f"Видео: файл готов {output_path}")
        return VideoResult(
            output_path=output_path,
            background_path=selection.path,
            subtitles_path=subtitles_path,
            duration_seconds=render_duration_seconds,
            scene_count=len(prepared_segments),
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
