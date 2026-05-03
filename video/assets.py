from __future__ import annotations

import hashlib
import os
import random
import shutil
from datetime import datetime
from pathlib import Path
from urllib import error
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import VideoConfig


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
DEFAULT_PLAYLIST_SIZE = 8


def resolve_background(config: VideoConfig, requested_path: Path | None, auto_fetch: bool) -> Path:
    return resolve_backgrounds(config, requested_path, auto_fetch, DEFAULT_PLAYLIST_SIZE)[0]


def resolve_backgrounds(
    config: VideoConfig,
    requested_path: Path | None,
    auto_fetch: bool,
    target_count: int = DEFAULT_PLAYLIST_SIZE,
    tags: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    load_env_file(config.env_path)
    selected_tags = tuple(normalize_tag(tag) for tag in tags if normalize_tag(tag))
    if requested_path is not None:
        path = Path(requested_path).resolve()
        if not path.exists():
            raise SystemExit(f"Background video not found: {path}")
        log_video(f"Фон: выбран локальный файл {path}")
        return (path,)

    local = list_local_backgrounds(config, selected_tags)
    log_video(f"Фон: локальных клипов найдено {len(local)}")
    if local:
        selected = tuple(random.sample(local, k=min(len(local), target_count)))
        log_video(f"Фон: используем локальную библиотеку, выбрано {len(selected)}")
        return selected

    if auto_fetch:
        urls = select_background_urls(config, selected_tags)
        if urls:
            if selected_tags:
                log_video(f"Фон: скачиваем прямые ссылки по тегам: {', '.join(selected_tags)}")
            else:
                log_video("Фон: локальных клипов нет, скачиваем прямые ссылки из default_tags")
            fetched = fetch_url_backgrounds(config, target_count, urls)
            if fetched:
                return fetched
        else:
            log_video("Фон: прямые ссылки не настроены")

    raise SystemExit(
        "No background videos found. Put .mp4/.mov/.mkv/.webm files into "
        f"{config.backgrounds_dir} or add direct video URLs to the backgrounds section in {config.config_path}."
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def find_local_background(config: VideoConfig) -> Path | None:
    candidates = list_local_backgrounds(config)
    if not candidates:
        return None
    return random.choice(candidates).resolve()


def list_local_backgrounds(config: VideoConfig, tags: tuple[str, ...] = ()) -> list[Path]:
    if not config.backgrounds_dir.exists():
        return []

    candidates: list[Path] = []
    if tags:
        for tag in tags:
            tag_dir = config.backgrounds_dir / tag
            if tag_dir.exists():
                candidates.extend(iter_video_files(tag_dir))
        if candidates:
            return sorted({path.resolve() for path in candidates})
        log_video("Фон: по тегам локальных клипов нет, пробуем всю локальную библиотеку")

    candidates.extend(iter_video_files(config.backgrounds_dir))
    return sorted({path.resolve() for path in candidates})


def iter_video_files(directory: Path) -> list[Path]:
    return [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]


def fetch_url_background(config: VideoConfig, tags: tuple[str, ...] = ()) -> Path | None:
    fetched = fetch_url_backgrounds(config, 1, select_background_urls(config, tags))
    return fetched[0] if fetched else None


def fetch_url_backgrounds(config: VideoConfig, target_count: int, urls: tuple[str, ...]) -> tuple[Path, ...]:
    target_count = max(1, int(target_count))
    shuffled_urls = list(dict.fromkeys(urls))
    random.shuffle(shuffled_urls)
    log_video(f"Фон: цель {target_count} клип(ов), прямых ссылок {len(shuffled_urls)}")

    downloaded: list[Path] = []
    for url in shuffled_urls:
        if len(downloaded) >= target_count:
            break
        output_path = build_url_cache_path(config, url)
        if output_path.exists() and output_path.stat().st_size > 0:
            log_video(f"Фон: берём из кэша {output_path.name}")
            downloaded.append(output_path.resolve())
            continue
        log_video(f"Фон: скачиваем {safe_url_label(url)}")
        download_url(url, output_path)
        downloaded.append(output_path.resolve())

    log_video(f"Фон: готово клипов {len(downloaded)}")
    return tuple(downloaded)


def build_url_cache_path(config: VideoConfig, url: str) -> Path:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        suffix = ".mp4"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return config.cache_dir / "backgrounds" / f"url_{digest}{suffix}"


def download_url(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url=url, headers={"User-Agent": "rdt-to-tt-video/0.2"})
    temp_path = output_path.with_suffix(output_path.suffix + ".part")
    try:
        with urlopen(request, timeout=120) as response:
            temp_path.write_bytes(response.read())
        temp_path.replace(output_path)
    except error.URLError as exc:
        if temp_path.exists():
            temp_path.unlink()
        raise SystemExit(f"Background video download failed: {exc.reason}") from exc


def select_background_urls(config: VideoConfig, tags: tuple[str, ...] = ()) -> tuple[str, ...]:
    normalized_tags = tuple(normalize_tag(tag) for tag in tags if normalize_tag(tag))
    unknown_tags = tuple(tag for tag in normalized_tags if tag not in config.background_url_groups)
    if unknown_tags:
        known = ", ".join(sorted(config.background_url_groups))
        raise SystemExit(f"Unknown background tag(s): {', '.join(unknown_tags)}. Known tags: {known}.")

    selected: list[str] = []
    for tag in normalized_tags or config.background_default_tags:
        selected.extend(config.background_url_groups.get(normalize_tag(tag), ()))

    if not selected:
        selected.extend(config.background_urls)

    return tuple(dict.fromkeys(url.strip() for url in selected if url.strip()))


def cleanup_temporary_backgrounds(config: VideoConfig, paths: tuple[Path, ...]) -> None:
    temp_root = (config.cache_dir / "tmp").resolve()
    deleted_files = 0
    deleted_dirs: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        try:
            path.relative_to(temp_root)
        except ValueError:
            continue
        if path.exists() and path.is_file():
            path.unlink()
            deleted_files += 1
        parent = path.parent
        if parent != temp_root:
            deleted_dirs.add(parent)

    for directory in sorted(deleted_dirs, key=lambda item: len(item.parts), reverse=True):
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    if deleted_files:
        log_video(f"Фон: временные файлы удалены, файлов {deleted_files}")


def normalize_tag(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def safe_url_label(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name or parsed.netloc or "video"
    return name[:80]


def log_video(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)
