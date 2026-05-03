from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_downloader_settings


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def default_media_dir() -> Path:
    return load_downloader_settings(DEFAULT_CONFIG_PATH).config.default_output_dir


def list_videos(directory: Path | str | None = None) -> list[dict[str, Any]]:
    root = resolve_media_dir(directory)
    if not root.exists():
        return []
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [describe_video(path) for path in files]


def describe_video(path: Path | str) -> dict[str, Any]:
    resolved = resolve_media_path(path)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "name": resolved.name,
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "duration": probe_duration(resolved),
    }


def probe_duration(path: Path | str) -> float | None:
    resolved = resolve_media_path(path)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(resolved),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
        return float(payload["format"]["duration"])
    except Exception:
        return None


def has_audio_stream(path: Path | str) -> bool:
    resolved = resolve_media_path(path)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(resolved),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def trim_video(
    input_path: Path | str,
    start_seconds: float,
    end_seconds: float,
    precise: bool = False,
    output_name: str | None = None,
) -> Path:
    source = resolve_media_path(input_path)
    validate_range(start_seconds, end_seconds)
    target = named_output_path(source, output_name) if output_name else unique_output_path(source, f"trim_{format_stamp(start_seconds)}_{format_stamp(end_seconds)}")
    if precise:
        args = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-to",
            f"{end_seconds:.3f}",
            "-i",
            str(source),
            "-c:v",
            "libx264",
        ]
        if has_audio_stream(source):
            args.extend(["-c:a", "aac"])
        else:
            args.append("-an")
        args.append(str(target))
    else:
        args = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-to",
            f"{end_seconds:.3f}",
            "-i",
            str(source),
            "-c",
            "copy",
            str(target),
        ]
    run_ffmpeg(args)
    return target


def cut_video(
    input_path: Path | str,
    start_seconds: float,
    end_seconds: float,
    precise: bool = False,
    output_name: str | None = None,
) -> Path:
    source = resolve_media_path(input_path)
    validate_range(start_seconds, end_seconds)
    target = named_output_path(source, output_name) if output_name else unique_output_path(source, f"cut_{format_stamp(start_seconds)}_{format_stamp(end_seconds)}")
    duration = probe_duration(source)
    if duration is not None and end_seconds >= duration:
        return trim_video(source, 0.0, start_seconds, precise=precise, output_name=output_name)

    if precise:
        if has_audio_stream(source):
            filter_expr = (
                f"[0:v]trim=0:{start_seconds:.3f},setpts=PTS-STARTPTS[v0];"
                f"[0:a]atrim=0:{start_seconds:.3f},asetpts=PTS-STARTPTS[a0];"
                f"[0:v]trim={end_seconds:.3f},setpts=PTS-STARTPTS[v1];"
                f"[0:a]atrim={end_seconds:.3f},asetpts=PTS-STARTPTS[a1];"
                "[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]"
            )
            args = [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                filter_expr,
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(target),
            ]
        else:
            filter_expr = (
                f"[0:v]trim=0:{start_seconds:.3f},setpts=PTS-STARTPTS[v0];"
                f"[0:v]trim={end_seconds:.3f},setpts=PTS-STARTPTS[v1];"
                "[v0][v1]concat=n=2:v=1:a=0[outv]"
            )
            args = [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                filter_expr,
                "-map",
                "[outv]",
                "-c:v",
                "libx264",
                "-an",
                str(target),
            ]
    else:
        # Stream-copy concat is fast, but depends on keyframes. It is good enough for rough editing.
        temp_dir = source.parent / f".{target.stem}_parts"
        temp_dir.mkdir(exist_ok=True)
        first = temp_dir / f"{target.stem}_a{source.suffix}"
        second = temp_dir / f"{target.stem}_b{source.suffix}"
        list_file = temp_dir / "concat.txt"
        try:
            run_ffmpeg(["ffmpeg", "-y", "-i", str(source), "-to", f"{start_seconds:.3f}", "-c", "copy", str(first)])
            run_ffmpeg(["ffmpeg", "-y", "-ss", f"{end_seconds:.3f}", "-i", str(source), "-c", "copy", str(second)])
            list_file.write_text(
                f"file '{escape_concat_path(first)}'\nfile '{escape_concat_path(second)}'\n",
                encoding="utf-8",
            )
            run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(target)])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return target


def export_timeline(
    clips: list[dict[str, Any]],
    precise: bool = True,
    output_name: str | None = None,
) -> Path:
    prepared = prepare_timeline_clips(clips)
    if not prepared:
        raise ValueError("timeline has no clips")

    source = prepared[0]["source"]
    target = named_output_path(source, output_name) if output_name else unique_output_path(source, "timeline")
    has_audio = has_audio_stream(source)

    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    args = ["ffmpeg", "-y", "-i", str(source)]
    for index, clip in enumerate(prepared):
        start = clip["source_start"]
        end = clip["source_end"]
        filter_parts.append(f"[0:v]trim={start:.3f}:{end:.3f},setpts=PTS-STARTPTS[v{index}]")
        concat_inputs.append(f"[v{index}]")
        if has_audio:
            filter_parts.append(f"[0:a]atrim={start:.3f}:{end:.3f},asetpts=PTS-STARTPTS[a{index}]")
            concat_inputs.append(f"[a{index}]")

    filter_parts.append(
        "".join(concat_inputs)
        + f"concat=n={len(prepared)}:v=1:a={1 if has_audio else 0}"
        + ("[outv][outa]" if has_audio else "[outv]")
    )
    args.extend(["-filter_complex", ";".join(filter_parts), "-map", "[outv]"])
    if has_audio:
        args.extend(["-map", "[outa]", "-c:a", "aac"])
    else:
        args.append("-an")
    args.extend(["-c:v", "libx264", str(target)])
    run_ffmpeg(args)
    return target


def prepare_timeline_clips(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    first_source: Path | None = None
    for clip in clips:
        source = resolve_media_path(str(clip.get("source") or ""))
        if first_source is None:
            first_source = source
        elif source.resolve() != first_source.resolve():
            raise ValueError("simple timeline export supports one source file for now")

        source_start = float(clip.get("source_start"))
        source_end = float(clip.get("source_end"))
        timeline_start = float(clip.get("timeline_start", 0))
        validate_range(source_start, source_end)
        prepared.append(
            {
                "source": source,
                "source_start": source_start,
                "source_end": source_end,
                "timeline_start": max(0.0, timeline_start),
            }
        )

    prepared.sort(key=lambda item: item["timeline_start"])
    return prepared


def resolve_media_dir(value: Path | str | None) -> Path:
    if value is None or not str(value).strip():
        return default_media_dir().resolve()
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def resolve_media_path(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"not a supported video file: {resolved}")
    return resolved


def validate_range(start_seconds: float, end_seconds: float) -> None:
    if start_seconds < 0:
        raise ValueError("start must be >= 0")
    if end_seconds <= start_seconds:
        raise ValueError("end must be greater than start")


def unique_output_path(source: Path, operation: str) -> Path:
    clean_operation = re.sub(r"[^A-Za-z0-9_.-]+", "_", operation).strip("_")
    base = source.with_name(f"{source.stem}_{clean_operation}{source.suffix}")
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = source.with_name(f"{source.stem}_{clean_operation}_{index}{source.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot create unique output path for {source.name}")


def named_output_path(source: Path, output_name: str | None) -> Path:
    if not output_name or not output_name.strip():
        raise ValueError("output name is empty")
    clean_name = re.sub(r"[^A-Za-z0-9а-яА-ЯёЁ_. -]+", "_", output_name.strip()).strip(" ._")
    if not clean_name:
        raise ValueError("output name is empty")
    target = source.with_name(f"{clean_name}{source.suffix}")
    if target.resolve() == source.resolve():
        target = source.with_name(f"{clean_name}_edited{source.suffix}")
    if not target.exists():
        return target
    stem = target.stem
    for index in range(1, 1000):
        candidate = target.with_name(f"{stem}_{index}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot create unique output path for {clean_name}")


def format_stamp(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}-{minutes:02d}-{sec:02d}-{millis:03d}"


def run_ffmpeg(args: list[str]) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH")
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(message[-2000:])


def escape_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
