from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from .base import DownloadOptions, DownloadResult
from .providers.youtube import download_youtube, fetch_youtube_info


ProgressCallback = Callable[[dict[str, Any]], None]


def download(
    urls: str | Iterable[str],
    options: DownloadOptions | Mapping[str, Any] | None = None,
    progress_cb: ProgressCallback | None = None,
    **overrides: Any,
) -> tuple[DownloadResult, ...]:
    opts = _build_options(options, overrides)
    if opts.provider == "youtube":
        return download_youtube(urls, opts, progress_cb=progress_cb)
    raise ValueError(f"Unsupported downloader provider: {opts.provider}")


def fetch_info(
    url: str,
    options: DownloadOptions | Mapping[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    opts = _build_options(options, overrides)
    if opts.provider == "youtube":
        return fetch_youtube_info(url, opts)
    raise ValueError(f"Unsupported downloader provider: {opts.provider}")


def available_providers() -> tuple[str, ...]:
    return ("youtube",)


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
    values["provider"] = str(values.get("provider") or "youtube").strip().lower()
    return DownloadOptions(**values)
