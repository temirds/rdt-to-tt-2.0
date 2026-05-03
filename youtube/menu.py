from __future__ import annotations

from datetime import datetime
import time
from pathlib import Path
import shutil
import json
import os
import subprocess
import re

from app.core.ui_menu import prompt_choice
from app.features.youtube.downloader import DownloadOptions, download_urls, fetch_info
from app import settings


def _prompt_text(title: str, default: str | None = None) -> str:
    try:
        import questionary
    except ImportError:
        questionary = None

    if questionary:
        return questionary.text(title, default=default or "").ask() or ""

    suffix = f" [{default}]" if default else ""
    value = input(f"{title}{suffix}: ").strip()
    return value or (default or "")


def _log(stage: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def _format_from_quality(choice: str) -> str:
    if choice == "best":
        return "bestvideo+bestaudio/best"
    if choice == "1080p":
        return (
            "bestvideo[height=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height=1080]+bestaudio/"
            "best[height=1080]/best"
        )
    if choice == "1440p":
        return (
            "bestvideo[height=1440][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height=1440]+bestaudio/"
            "best[height=1440]/best"
        )
    if choice == "2160p":
        return (
            "bestvideo[height=2160][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height=2160]+bestaudio/"
            "best[height=2160]/best"
        )
    if choice == "720p":
        return (
            "bestvideo[height=720][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height=720]+bestaudio/"
            "best[height=720]/best"
        )
    if choice == "480p":
        return (
            "bestvideo[height=480][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height=480]+bestaudio/"
            "best[height=480]/best"
        )
    if choice == "360p":
        return (
            "bestvideo[height=360][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height=360]+bestaudio/"
            "best[height=360]/best"
        )
    return "bestvideo+bestaudio/best"

def _format_from_height(height: int, with_audio: bool) -> str:
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

def _download_selectors(height: int, with_audio: bool) -> list[str]:
    if with_audio:
        return [
            f"bestvideo[height<={height}][ext=mp4][protocol=https]+bestaudio[ext=m4a][protocol=https]",
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]",
            f"best[height<={height}][ext=mp4][protocol=https]",
            f"best[height<={height}][ext=mp4]",
            f"best[height<={height}]/best",
        ]
    return [
        f"bestvideo[height<={height}][ext=mp4][protocol=https]",
        f"bestvideo[height<={height}][ext=mp4]",
        f"best[height<={height}][ext=mp4][protocol=https]",
        f"best[height<={height}][ext=mp4]",
        f"best[height<={height}]/best",
    ]


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _node_available() -> bool:
    return shutil.which("node") is not None


def _format_filesize(value: int | None) -> str:
    if not value:
        return ""
    size = float(value)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if size < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}PiB"

def _resolve_js_runtimes() -> list[str] | None:
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
    return [f"node:{path}"]

def _print_formats(formats: list[dict]) -> None:
    if not formats:
        _log("warning", "Список форматов пуст.")
        return
    header = f"{'ID':<6} {'EXT':<5} {'RES':<10} {'FPS':<4} {'VCODEC':<12} {'ACODEC':<10} {'TBR':<6}"
    print(header)
    print("-" * len(header))
    for fmt in formats:
        fmt_id = str(fmt.get("format_id") or "")
        ext = str(fmt.get("ext") or "")
        height = fmt.get("height")
        width = fmt.get("width")
        res = fmt.get("resolution") or (f"{width}x{height}" if width and height else "")
        fps = fmt.get("fps") or ""
        vcodec = fmt.get("vcodec") or ""
        acodec = fmt.get("acodec") or ""
        tbr = fmt.get("tbr")
        tbr_str = f"{int(tbr)}k" if isinstance(tbr, (int, float)) else ""
        print(f"{fmt_id:<6} {ext:<5} {res:<10} {str(fps):<4} {vcodec:<12} {acodec:<10} {tbr_str:<6}")


def _get_format_height(fmt: dict) -> int | None:
    height = fmt.get("height")
    if isinstance(height, (int, float)) and height > 0:
        return int(height)
    if isinstance(height, str):
        match = re.search(r"(\d{3,4})", height)
        if match:
            return int(match.group(1))

    resolution = str(fmt.get("resolution") or "").strip()
    match = re.search(r"(\d{3,5})[xX](\d{3,5})", resolution)
    if match:
        return int(match.group(2))
    match = re.search(r"(\d{3,4})p\b", resolution, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))

    for key in ("format_note", "format", "quality"):
        value = str(fmt.get(key) or "").strip()
        match = re.search(r"(\d{3,4})p\b", value, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

def _pick_video_format(formats: list[dict], height: int, prefer_ext: str = "mp4") -> dict | None:
    candidates: list[tuple[bool, bool, float, dict]] = []
    for fmt in formats:
        if fmt.get("vcodec") == "none":
            continue
        if fmt.get("acodec") != "none":
            continue
        if _get_format_height(fmt) != height:
            continue
        ext = fmt.get("ext")
        protocol = str(fmt.get("protocol") or "")
        is_https = protocol == "https"
        tbr = fmt.get("tbr") or 0
        candidates.append((ext == prefer_ext, is_https, float(tbr), fmt))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]

def _pick_video_format_closest(formats: list[dict], height: int, prefer_ext: str = "mp4") -> dict | None:
    exact = _pick_video_format(formats, height, prefer_ext=prefer_ext)
    if exact:
        return exact
    heights = sorted(
        {
            parsed_height
            for fmt in formats
            if (parsed_height := _get_format_height(fmt))
            and fmt.get("vcodec") != "none"
            and fmt.get("acodec") == "none"
        }
    )
    if not heights:
        return None
    # Pick closest lower height, otherwise the smallest higher one.
    lower = [h for h in heights if h <= height]
    target = max(lower) if lower else min(heights)
    return _pick_video_format(formats, target, prefer_ext=prefer_ext)


def _pick_audio_format(formats: list[dict], prefer_ext: str = "m4a") -> dict | None:
    candidates: list[tuple[bool, bool, float, dict]] = []
    for fmt in formats:
        if fmt.get("vcodec") != "none":
            continue
        if fmt.get("acodec") == "none":
            continue
        ext = fmt.get("ext")
        protocol = str(fmt.get("protocol") or "")
        is_https = protocol == "https"
        abr = fmt.get("abr") or 0
        candidates.append((ext == prefer_ext, is_https, float(abr), fmt))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]

def _pick_muxed_format(formats: list[dict], height: int, prefer_ext: str = "mp4") -> dict | None:
    candidates: list[tuple[bool, bool, float, dict]] = []
    for fmt in formats:
        if fmt.get("vcodec") == "none":
            continue
        if fmt.get("acodec") == "none":
            continue
        if _get_format_height(fmt) != height:
            continue
        ext = fmt.get("ext")
        protocol = str(fmt.get("protocol") or "")
        is_https = protocol == "https"
        tbr = fmt.get("tbr") or 0
        candidates.append((ext == prefer_ext, is_https, float(tbr), fmt))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]

def _pick_muxed_format_closest(formats: list[dict], height: int, prefer_ext: str = "mp4") -> dict | None:
    exact = _pick_muxed_format(formats, height, prefer_ext=prefer_ext)
    if exact:
        return exact
    heights = sorted(
        {
            parsed_height
            for fmt in formats
            if (parsed_height := _get_format_height(fmt))
            and fmt.get("vcodec") != "none"
            and fmt.get("acodec") != "none"
        }
    )
    if not heights:
        return None
    lower = [h for h in heights if h <= height]
    target = max(lower) if lower else min(heights)
    return _pick_muxed_format(formats, target, prefer_ext=prefer_ext)

def _format_quality_label(base: str, fmt: dict | None) -> str:
    if not fmt:
        return base
    fmt_id = fmt.get("format_id")
    return f"{base} ({fmt_id})" if fmt_id else base

def _available_heights(formats: list[dict]) -> list[int]:
    heights = {
        parsed_height
        for fmt in formats
        if (parsed_height := _get_format_height(fmt)) and fmt.get("vcodec") != "none"
    }
    return sorted(heights)


def _has_playable_formats(formats: list[dict]) -> bool:
    for fmt in formats:
        if fmt.get("vcodec") != "none" or fmt.get("acodec") != "none":
            return True
    return False


def _convert_json_cookies(json_path: Path, output_path: Path) -> Path | None:
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log("warning", f"Не удалось прочитать cookies.json: {exc}")
        return None

    if isinstance(raw, dict) and "cookies" in raw:
        cookies = raw.get("cookies") or []
    else:
        cookies = raw

    if not isinstance(cookies, list):
        _log("warning", "cookies.json имеет неизвестный формат.")
        return None

    lines = [
        "# Netscape HTTP Cookie File",
        "# This file was generated from cookies.json",
    ]
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        path = item.get("path", "/")
        if not name or value is None or not domain:
            continue
        http_only = bool(item.get("httpOnly") or item.get("http_only"))
        secure = bool(item.get("secure"))
        host_only = item.get("hostOnly")
        if host_only is None:
            host_only = not str(domain).startswith(".")
        domain_str = str(domain)
        if http_only:
            domain_str = f"#HttpOnly_{domain_str}"
        include_subdomains = "FALSE" if host_only else "TRUE"
        expiry = (
            item.get("expiry")
            or item.get("expirationDate")
            or item.get("expires")
            or 0
        )
        try:
            expiry = int(expiry)
        except Exception:
            expiry = 0
        line = "\t".join(
            [
                domain_str,
                include_subdomains,
                path,
                "TRUE" if secure else "FALSE",
                str(expiry),
                str(name),
                str(value),
            ]
        )
        lines.append(line)

    if len(lines) <= 2:
        _log("warning", "cookies.json не содержит подходящих cookies.")
        return None

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _resolve_cookies_path() -> Path | None:
    base_dir = Path(__file__).resolve().parent
    json_path = base_dir / "cookies.json"
    if json_path.exists():
        converted = _convert_json_cookies(json_path, base_dir / "cookies.txt")
        if converted:
            return converted
    txt_path = base_dir / "cookies.txt"
    if txt_path.exists():
        return txt_path
    return None


def run() -> None:
    print("============================================")
    print("          YOUTUBE DOWNLOADER MENU")
    print("============================================")

    action = prompt_choice(
        "Select action:",
        ["download", "list formats", "parse cookies", "back"],
        default="download",
    )
    if action == "back" or not action:
        return
    if action == "parse cookies":
        base_dir = Path(__file__).resolve().parent
        json_path = base_dir / "cookies.json"
        if not json_path.exists():
            _log("error", "cookies.json not found in current folder.")
            return
        converted = _convert_json_cookies(json_path, base_dir / "cookies.txt")
        if converted:
            _log("done", f"Parsed cookies -> {converted}")
        return

    url = _prompt_text("Enter video URL")
    if not url:
        _log("error", "URL не задан.")
        return

    cookies_path = _resolve_cookies_path()
    if not cookies_path:
        _log("info", "cookies.json/cookies.txt not found in current folder.")
    js_runtimes = _resolve_js_runtimes()
    if not js_runtimes:
        _log(
            "warning",
            "Node.js не найден. YouTube может вернуть неполный список форматов или 403 на часть потоков. "
            "Установи Node.js и убедись, что `node` доступен в PATH.",
        )
    else:
        _log("info", f"JS runtime: {', '.join(js_runtimes)}")
    info_options = DownloadOptions(
        output_dir=settings.VIDEOS_RAW_DIR,
        audio_only=False,
        prefer_mp4=settings.YOUTUBE_PREFER_MP4,
        format_selector=None,
        cookies_path=cookies_path,
        js_runtimes=js_runtimes,
    )
    formats = []
    try:
        info = fetch_info(url, options=info_options)
        formats = info.get("formats") or []
        _log("info", f"Получено форматов: {len(formats)}")
        if cookies_path and not _has_playable_formats(formats):
            _log("warning", "cookies.txt вернул только storyboard/неиграбельные форматы, повторяю запрос без cookies.")
            cookies_path = None
            info_options.cookies_path = None
            info = fetch_info(url, options=info_options)
            formats = info.get("formats") or []
            _log("info", f"Получено форматов без cookies: {len(formats)}")
    except Exception as exc:
        _log("warning", f"Не удалось получить список форматов: {exc}")

    if action == "list formats":
        _print_formats(formats)
        return

    quality_map = {}
    quality_options = []
    heights = _available_heights(formats)
    if heights:
        for height in reversed(heights):
            base = f"{height}p"
            fmt = _pick_video_format(formats, height) or _pick_muxed_format(formats, height)
            label = _format_quality_label(base, fmt)
            quality_map[label] = base
            quality_options.append(label)
    else:
        _log(
            "error",
            "Не удалось определить доступные качества из списка форматов.",
        )
        return

    default_quality = settings.YOUTUBE_DEFAULT_QUALITY
    if default_quality not in quality_map:
        default_quality = quality_options[0]
    default_label = next((lbl for lbl, key in quality_map.items() if key == default_quality), None)
    if not default_label:
        default_label = quality_options[0]
    quality_label = prompt_choice(
        "Select quality:",
        quality_options,
        default=default_label,
    )
    quality = quality_map.get(quality_label)
    if not quality:
        _log("error", "Качество не выбрано.")
        return

    with_audio = prompt_choice(
        "Include audio?",
        ["yes", "no"],
        default="yes" if settings.YOUTUBE_WITH_AUDIO else "no",
    )
    if not with_audio:
        with_audio = "yes"

    _log("start", f"Скачивание: {url}")
    _log("options", f"Качество: {quality}")

    options = DownloadOptions(
        output_dir=settings.VIDEOS_RAW_DIR,
        audio_only=False,
        prefer_mp4=settings.YOUTUBE_PREFER_MP4,
        format_selector=_format_from_quality(quality),
        cookies_path=cookies_path,
        js_runtimes=js_runtimes,
        youtube_player_clients=None,
    )
    if formats:
        height = int(quality.replace("p", ""))
        options.format_selector = _format_from_height(height, with_audio=with_audio != "no")
        video_fmt = _pick_video_format_closest(formats, height, prefer_ext="mp4")
        audio_fmt = _pick_audio_format(formats, prefer_ext="m4a") if with_audio != "no" else None
        muxed_fmt = _pick_muxed_format_closest(formats, height, prefer_ext="mp4")
        if with_audio == "no":
            if video_fmt:
                _log("info", f"Предпочтительный формат id: {video_fmt['format_id']}")
            elif muxed_fmt:
                _log("warning", "Отдельный video-only поток не найден, использую объединённый поток с аудио.")
                _log("info", f"Предпочтительный формат id: {muxed_fmt['format_id']}")
        else:
            if video_fmt and audio_fmt:
                _log("info", f"Предпочтительный формат id: {video_fmt['format_id']}+{audio_fmt['format_id']}")
            elif muxed_fmt:
                _log("warning", "Отдельные video/audio потоки не найдены, использую объединённый поток.")
                _log("info", f"Предпочтительный формат id: {muxed_fmt['format_id']}")
            elif video_fmt:
                _log("warning", "Аудио поток не найден, скачиваю только видео.")
    if "+bestaudio" in options.format_selector and not _ffmpeg_available():
        _log(
            "warning",
            "ffmpeg не найден. Высокие качества (720/1080) могут быть недоступны, "
            "будет скачано более низкое качество. Установи ffmpeg и проверь PATH.",
        )

    last_status = {"value": None}
    last_progress = {"percent": None, "ts": 0.0}

    def on_progress(data):
        status = data.get("status")
        if status != last_status["value"]:
            last_status["value"] = status
            if status == "downloading":
                print("Загрузка: 0%", end="\r", flush=True)
            elif status == "finished":
                print("", end="\n", flush=True)
                _log("merge", "Сборка/объединение...")
        if status == "downloading":
            now = time.monotonic()
            percent = data.get("_percent_str", "").strip()
            speed = data.get("_speed_str", "").strip()
            eta = data.get("_eta_str", "").strip()
            total = data.get("_total_bytes_str", "").strip()
            percent_key = percent or None
            if (
                percent_key != last_progress["percent"]
                or now - last_progress["ts"] >= 1.0
            ):
                last_progress["percent"] = percent_key
                last_progress["ts"] = now
                parts = []
                if percent:
                    parts.append(percent)
                if total:
                    parts.append(f"of {total}")
                if speed:
                    parts.append(f"at {speed}")
                if eta:
                    parts.append(f"ETA {eta}")
                line = "Загрузка: " + (" ".join(parts) if parts else "")
                print(line, end="\r", flush=True)

    selectors = [options.format_selector]
    if formats:
        selectors = _download_selectors(height, with_audio=with_audio != "no")

    result = None
    for idx, selector in enumerate(selectors, start=1):
        options.format_selector = selector
        if len(selectors) > 1:
            _log("info", f"Попытка {idx}/{len(selectors)}: {selector}")
        results = download_urls(url, options=options, progress_cb=on_progress)
        candidate = results[0]
        if candidate.success:
            result = candidate
            break
        err = (candidate.error or "").lower()
        retryable = "403" in err or "forbidden" in err or "requested format is not available" in err
        if not retryable:
            result = candidate
            break
        result = candidate

    if result is None:
        _log("error", "Download failed: unknown error")
        return

    if result.success:
        _log("done", "Готово.")
        if result.info:
            height = result.info.get("height")
            fmt = result.info.get("format_id")
            if height or fmt:
                _log("info", f"Фактический формат: {fmt or 'unknown'}, высота: {height or 'unknown'}")
            req = result.info.get("requested_downloads") or []
            for item in req:
                vheight = item.get("height")
                vfmt = item.get("format_id")
                if vheight or vfmt:
                    _log("info", f"Поток: {vfmt or 'unknown'}, высота: {vheight or 'unknown'}")
        for path in result.filepaths:
            print(f"- {path}")
    else:
        _log("error", f"Download failed: {result.error}")
