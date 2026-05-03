from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yt_dlp
import re
import shutil

from app import settings


@dataclass(slots=True)
class DownloadOptions:
    output_dir: Path = settings.VIDEOS_RAW_DIR
    filename_template: str = "%(title)s.%(ext)s"
    audio_only: bool = False
    prefer_mp4: bool = True
    format_selector: str | None = None
    cookies_path: Path | None = None
    user_agent: str | None = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    referer: str | None = "https://www.youtube.com/"
    youtube_player_clients: list[str] | None = None
    no_playlist: bool = True
    retries: int = 20
    fragment_retries: int = 20
    extractor_retries: int = 10
    concurrent_fragments: int = 1
    socket_timeout: int = 30
    http_chunk_size: int | None = None
    retry_sleep: dict[str, int] = field(default_factory=lambda: {"fragment": 2, "http": 2})
    merge_output_format: str | None = None
    subtitles: bool = False
    auto_subtitles: bool = False
    subtitles_langs: list[str] = field(default_factory=lambda: ["en", "ru"])
    ffmpeg_location: Path | None = None
    js_runtimes: dict[str, dict[str, Any]] | list[str] | None = None
    cookies_from_browser: str | list[str] | tuple[str, ...] | None = None
    remote_components: list[str] | None = None


@dataclass(slots=True)
class DownloadResult:
    url: str
    success: bool
    filepaths: list[str]
    info: dict[str, Any] | None
    error: str | None


ProgressCallback = Callable[[dict[str, Any]], None]


def _default_format_selector(options: DownloadOptions) -> str:
    if options.audio_only:
        return "bestaudio/best"
    if options.prefer_mp4:
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    return "bestvideo+bestaudio/best"


def _build_ydl_opts(
    options: DownloadOptions,
    progress_cb: ProgressCallback | None,
) -> dict[str, Any]:
    output_dir = Path(options.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(output_dir / options.filename_template)

    ydl_opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "noplaylist": options.no_playlist,
        "retries": options.retries,
        "fragment_retries": options.fragment_retries,
        "extractor_retries": options.extractor_retries,
        "concurrent_fragment_downloads": options.concurrent_fragments,
        "socket_timeout": options.socket_timeout,
        "retry_sleep": options.retry_sleep,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
    }

    fmt = options.format_selector or _default_format_selector(options)
    ydl_opts["format"] = fmt

    if options.merge_output_format:
        ydl_opts["merge_output_format"] = options.merge_output_format

    if options.http_chunk_size:
        ydl_opts["http_chunk_size"] = options.http_chunk_size

    if options.cookies_path and options.cookies_path.exists():
        ydl_opts["cookiefile"] = str(options.cookies_path)

    if options.user_agent:
        ydl_opts["user_agent"] = options.user_agent

    if options.referer:
        ydl_opts.setdefault("http_headers", {})
        ydl_opts["http_headers"]["Referer"] = options.referer

    if options.youtube_player_clients:
        ydl_opts.setdefault("extractor_args", {})
        ydl_opts["extractor_args"]["youtube"] = {
            "player_client": options.youtube_player_clients
        }

    if options.ffmpeg_location:
        ydl_opts["ffmpeg_location"] = str(options.ffmpeg_location)

    if options.js_runtimes:
        # yt-dlp expects {"runtime": {config}}; support legacy list input like ["node:C:\\path\\node.exe"].
        if isinstance(options.js_runtimes, dict):
            ydl_opts["js_runtimes"] = options.js_runtimes
        else:
            runtimes: dict[str, dict[str, Any]] = {}
            for item in options.js_runtimes:
                if not isinstance(item, str) or not item.strip():
                    continue
                if ":" in item:
                    runtime, value = item.split(":", 1)
                    runtime = runtime.strip()
                    value = value.strip()
                    if runtime:
                        runtimes[runtime] = {"path": value} if value else {}
                else:
                    runtimes[item.strip()] = {}
            if runtimes:
                ydl_opts["js_runtimes"] = runtimes

    if options.cookies_from_browser:
        if isinstance(options.cookies_from_browser, str):
            ydl_opts["cookiesfrombrowser"] = [options.cookies_from_browser]
        else:
            ydl_opts["cookiesfrombrowser"] = options.cookies_from_browser

    if options.remote_components:
        ydl_opts["remote_components"] = options.remote_components

    if options.audio_only:
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    if options.subtitles or options.auto_subtitles:
        ydl_opts["writesubtitles"] = options.subtitles
        ydl_opts["writeautomaticsub"] = options.auto_subtitles
        ydl_opts["subtitleslangs"] = options.subtitles_langs

    if progress_cb:
        ydl_opts["progress_hooks"] = [progress_cb]

    return ydl_opts


def _collect_filepaths(info: dict[str, Any]) -> list[str]:
    filepaths: list[str] = []
    if "entries" in info and isinstance(info["entries"], list):
        for entry in info["entries"]:
            if not entry:
                continue
            filepaths.extend(_collect_filepaths(entry))
        return filepaths

    if "requested_downloads" in info:
        for item in info.get("requested_downloads", []):
            path = item.get("filepath") or item.get("filename")
            if path:
                filepaths.append(path)

    if "_filename" in info:
        filepaths.append(info["_filename"])
    elif "filepath" in info:
        filepaths.append(info["filepath"])

    return [str(Path(p)) for p in filepaths if p]


def _sanitize_title(title: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", " ", title or "").strip().lower()
    if not clean:
        return "video"
    parts = [p for p in clean.split(" ") if p]
    return "_".join(parts)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 1000):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot find unique filename for {path.name}")


def download_urls(
    urls: str | Iterable[str],
    options: DownloadOptions | None = None,
    progress_cb: ProgressCallback | None = None,
) -> list[DownloadResult]:
    if isinstance(urls, str):
        url_list = [urls]
    else:
        url_list = list(urls)

    options = options or DownloadOptions()
    results: list[DownloadResult] = []

    ydl_opts = _build_ydl_opts(options, progress_cb)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for url in url_list:
                try:
                    info = ydl.extract_info(url, download=True)
                    filepaths = _collect_filepaths(info) if info else []
                    renamed_any = False
                    if info and filepaths:
                        base = _sanitize_title(info.get("title") or info.get("id") or "video")
                        new_paths = []
                        total = len(filepaths)
                        for idx, fp in enumerate(filepaths, start=1):
                            path = Path(fp)
                            if not path.exists():
                                continue
                            stem = base if total == 1 else f"{base}_{idx}"
                            target = _unique_path(path.with_name(f"{stem}{path.suffix.lower()}"))
                            if path.resolve() != target.resolve():
                                shutil.move(str(path), str(target))
                            new_paths.append(str(target))
                            renamed_any = True
                        if renamed_any and new_paths:
                            filepaths = new_paths
                    results.append(
                        DownloadResult(
                            url=url,
                            success=True,
                            filepaths=filepaths,
                            info=info,
                            error=None,
                        )
                    )
                except Exception as exc:
                    results.append(
                        DownloadResult(
                            url=url,
                            success=False,
                            filepaths=[],
                            info=None,
                            error=str(exc),
                        )
                    )
    except Exception as exc:
        for url in url_list:
            results.append(
                DownloadResult(
                    url=url,
                    success=False,
                    filepaths=[],
                    info=None,
                    error=str(exc),
                )
            )
    return results


def fetch_info(
    url: str,
    options: DownloadOptions | None = None,
) -> dict[str, Any]:
    options = options or DownloadOptions()
    ydl_opts = _build_ydl_opts(options, progress_cb=None)
    ydl_opts["skip_download"] = True
    ydl_opts["format"] = "best"
    ydl_opts["ignore_no_formats_error"] = True
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)
