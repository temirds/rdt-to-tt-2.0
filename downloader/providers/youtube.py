from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..base import DownloadOptions, DownloadResult
from ..config import DEFAULT_CONFIG_PATH, DownloaderConfig, load_downloader_settings
from ..filenames import normalize_file_paths


ProgressCallback = Callable[[dict[str, Any]], None]


def download_youtube(
    urls: str | Iterable[str],
    options: DownloadOptions | Mapping[str, Any] | None = None,
    progress_cb: ProgressCallback | None = None,
    **overrides: Any,
) -> tuple[DownloadResult, ...]:
    yt_dlp = _load_yt_dlp()
    opts = _build_options(options, overrides)
    config = load_downloader_settings(Path(opts.config_path) if opts.config_path else DEFAULT_CONFIG_PATH).config
    ydl_opts = _build_ydl_options(config, opts, progress_cb=progress_cb)
    url_list = (urls,) if isinstance(urls, str) else tuple(urls)
    if not url_list:
        raise SystemExit("Pass at least one YouTube URL.")

    results: list[DownloadResult] = []
    selectors = _download_selectors(opts, config)
    for url in url_list:
        result: DownloadResult | None = None
        for selector in selectors:
            ydl_opts["format"] = selector
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                result = _build_result(url, info, success=True, error=None)
                break
            except Exception as exc:
                result = DownloadResult(
                    url=url,
                    success=False,
                    filepaths=(),
                    provider="youtube",
                    error=str(exc),
                )
                if not _is_retryable_format_error(str(exc)):
                    break
        if result is not None:
            results.append(result)
    return tuple(results)


def fetch_youtube_info(
    url: str,
    options: DownloadOptions | Mapping[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    yt_dlp = _load_yt_dlp()
    opts = _build_options(options, overrides)
    config = load_downloader_settings(Path(opts.config_path) if opts.config_path else DEFAULT_CONFIG_PATH).config
    ydl_opts = _build_ydl_options(config, opts, progress_cb=None)
    ydl_opts["skip_download"] = True
    ydl_opts["format"] = "best"
    ydl_opts["ignore_no_formats_error"] = True
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _build_options(
    options: DownloadOptions | Mapping[str, Any] | None,
    overrides: dict[str, Any],
) -> DownloadOptions:
    if options is None:
        values: dict[str, Any] = {}
    elif isinstance(options, DownloadOptions):
        values = {
            "provider": options.provider,
            "config_path": options.config_path,
            "output_dir": options.output_dir,
            "filename_template": options.filename_template,
            "quality": options.quality,
            "format_selector": options.format_selector,
            "with_audio": options.with_audio,
            "prefer_mp4": options.prefer_mp4,
            "cookies_path": options.cookies_path,
            "no_playlist": options.no_playlist,
            "retries": options.retries,
            "fragment_retries": options.fragment_retries,
            "socket_timeout": options.socket_timeout,
            "ffmpeg_location": options.ffmpeg_location,
        }
    else:
        values = dict(options)
    values.update({key: value for key, value in overrides.items() if value is not None})
    values["provider"] = "youtube"
    return DownloadOptions(**values)


def _build_ydl_options(
    config: DownloaderConfig,
    options: DownloadOptions,
    progress_cb: ProgressCallback | None,
) -> dict[str, Any]:
    output_dir = Path(options.output_dir) if options.output_dir is not None else config.default_output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    filename_template = options.filename_template or config.filename_template
    ydl_opts: dict[str, Any] = {
        "outtmpl": str(output_dir / filename_template),
        "format": _format_from_quality(
            options.quality or config.default_quality,
            with_audio=config.with_audio if options.with_audio is None else options.with_audio,
            prefer_mp4=config.prefer_mp4 if options.prefer_mp4 is None else options.prefer_mp4,
        ),
        "noplaylist": config.no_playlist if options.no_playlist is None else options.no_playlist,
        "retries": config.retries if options.retries is None else options.retries,
        "fragment_retries": config.fragment_retries
        if options.fragment_retries is None
        else options.fragment_retries,
        "extractor_retries": 10,
        "concurrent_fragment_downloads": 1,
        "socket_timeout": config.socket_timeout if options.socket_timeout is None else options.socket_timeout,
        "retry_sleep": {"fragment": 2, "http": 2},
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "noprogress": False,
        "overwrites": False,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "http_headers": {"Referer": "https://www.youtube.com/"},
    }

    js_runtimes = _resolve_js_runtimes()
    if js_runtimes:
        ydl_opts["js_runtimes"] = js_runtimes

    cookies_path = _resolve_existing_path(options.cookies_path) or config.cookies_path
    if cookies_path and cookies_path.exists():
        ydl_opts["cookiefile"] = str(cookies_path)

    ffmpeg_location = options.ffmpeg_location or config.ffmpeg_location
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = str(Path(ffmpeg_location).resolve())

    if progress_cb:
        ydl_opts["progress_hooks"] = [progress_cb]

    return ydl_opts


def _format_from_quality(quality: str, with_audio: bool, prefer_mp4: bool) -> str:
    normalized = quality.strip().lower()
    if normalized in {"best", "max"}:
        if with_audio:
            return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" if prefer_mp4 else "bestvideo+bestaudio/best"
        return "bestvideo[ext=mp4]/best[ext=mp4]/best" if prefer_mp4 else "bestvideo/best"
    if normalized in {"audio", "audio_only", "mp3"}:
        return "bestaudio/best"

    match = re.fullmatch(r"(\d{3,4})p?", normalized)
    if match:
        height = int(match.group(1))
        return _format_from_height(height, with_audio=with_audio, prefer_mp4=prefer_mp4)

    return quality


def _format_from_height(height: int, with_audio: bool, prefer_mp4: bool = True) -> str:
    if not prefer_mp4:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
            if with_audio
            else f"bestvideo[height<={height}]/best[height<={height}]/best"
        )
    if with_audio:
        return (
            f"bestvideo[height<={height}][ext=mp4][protocol=https]+bestaudio[ext=m4a][protocol=https]/"
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={height}][ext=mp4][protocol=https]/"
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/best"
        )
    return (
        f"bestvideo[height<={height}][ext=mp4][protocol=https]/"
        f"bestvideo[height<={height}][ext=mp4]/"
        f"best[height<={height}][ext=mp4][protocol=https]/"
        f"best[height<={height}][ext=mp4]/"
        f"best[height<={height}]/best"
    )


def _download_selectors(options: DownloadOptions, config: DownloaderConfig) -> list[str]:
    if options.format_selector:
        return [options.format_selector]

    quality = options.quality or config.default_quality
    prefer_mp4 = config.prefer_mp4 if options.prefer_mp4 is None else options.prefer_mp4
    with_audio = config.with_audio if options.with_audio is None else options.with_audio
    normalized = quality.strip().lower()
    match = re.fullmatch(r"(\d{3,4})p?", normalized)
    if not match:
        return [_format_from_quality(quality, with_audio=with_audio, prefer_mp4=prefer_mp4)]

    height = int(match.group(1))
    if not prefer_mp4:
        if not with_audio:
            return [
                f"bestvideo[height<={height}]",
                f"best[height<={height}]",
                "bestvideo/best",
            ]
        return [
            f"bestvideo[height<={height}]+bestaudio",
            f"best[height<={height}]",
            "best",
        ]

    if not with_audio:
        return [
            f"bestvideo[height<={height}][ext=mp4][protocol=https]",
            f"bestvideo[height<={height}][ext=mp4]",
            f"best[height<={height}][ext=mp4][protocol=https]",
            f"best[height<={height}][ext=mp4]",
            f"bestvideo[height<={height}]/best",
            "bestvideo/best",
        ]

    return [
        f"bestvideo[height<={height}][ext=mp4][protocol=https]+bestaudio[ext=m4a][protocol=https]",
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]",
        f"best[height<={height}][ext=mp4][protocol=https]",
        f"best[height<={height}][ext=mp4]",
        f"best[height<={height}]/best",
        "best",
    ]


def _is_retryable_format_error(error: str) -> bool:
    normalized = error.lower()
    return (
        "requested format is not available" in normalized
        or "403" in normalized
        or "forbidden" in normalized
    )


def _build_result(url: str, info: dict[str, Any] | None, success: bool, error: str | None) -> DownloadResult:
    filepaths = normalize_file_paths(_collect_filepaths(info or {}))
    return DownloadResult(
        url=url,
        success=success,
        filepaths=filepaths,
        provider="youtube",
        title=str(info.get("title")) if info and info.get("title") else None,
        video_id=str(info.get("id")) if info and info.get("id") else None,
        error=error,
        info=info,
    )


def _collect_filepaths(info: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for entry in info.get("entries") or ():
        if isinstance(entry, dict):
            paths.extend(_collect_filepaths(entry))
    for item in info.get("requested_downloads") or ():
        filepath = item.get("filepath") or item.get("filename")
        if filepath:
            paths.append(Path(filepath).resolve())
    for key in ("filepath", "_filename", "filename"):
        value = info.get(key)
        if value:
            paths.append(Path(value).resolve())
    return _unique_existing(paths)


def _unique_existing(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            result.append(path)
    return result


def _resolve_existing_path(value: Path | str | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(value).resolve()
    return path if path.exists() else None


def _resolve_js_runtimes() -> dict[str, dict[str, Any]] | None:
    path = shutil.which("node")
    if not path and os.name == "nt":
        try:
            result = subprocess.run(
                ["where", "node"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.stdout:
                path = result.stdout.splitlines()[0].strip()
        except Exception:
            path = None
    if not path:
        return None
    return {"node": {"path": path}}


def _load_yt_dlp():
    try:
        import yt_dlp
    except ImportError as exc:
        raise SystemExit(
            "Package yt-dlp is not installed. Run: python -m pip install -r downloader/requirements.txt"
        ) from exc
    if shutil.which("ffmpeg") is None:
        print("Warning: ffmpeg not found in PATH. High-quality video+audio merge may fail.")
    return yt_dlp
