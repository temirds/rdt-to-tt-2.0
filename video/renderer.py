from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import VideoConfig


def render_video(
    config: VideoConfig,
    background_path: Path,
    audio_path: Path,
    subtitles_path: Path,
    output_path: Path,
    duration_seconds: float,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_graph = ",".join(
        [
            f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase",
            f"crop={config.width}:{config.height}",
            f"fps={config.fps}",
            "eq=brightness=-0.08:contrast=1.05:saturation=0.9",
            f"subtitles='{escape_filter_path(subtitles_path)}'",
        ]
    )
    command = [
        config.ffmpeg_path,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(background_path),
        "-i",
        str(audio_path),
        "-t",
        f"{duration_seconds:.3f}",
        "-vf",
        filter_graph,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(output_path),
    ]
    run_command(command, "ffmpeg render failed")


def build_background_reel(
    config: VideoConfig,
    background_paths: tuple[Path, ...],
    output_path: Path,
    duration_seconds: float,
) -> Path:
    if len(background_paths) <= 1:
        return background_paths[0]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [config.ffmpeg_path, "-y"]
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, background_path in enumerate(background_paths):
        command.extend(["-i", str(background_path)])
        filter_parts.append(
            f"[{index}:v]"
            f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase,"
            f"crop={config.width}:{config.height},"
            f"fps={config.fps},setsar=1[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")

    filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(background_paths)}:v=1:a=0[vout]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            config.preset,
            "-crf",
            str(config.crf),
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    run_command(command, "ffmpeg background reel failed")
    return output_path


def probe_audio_duration(config: VideoConfig, audio_path: Path) -> float:
    command = [
        config.ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(audio_path),
    ]
    completed = run_command(command, "ffprobe failed", capture=True)
    payload = json.loads(completed.stdout)
    return float(payload["format"]["duration"])


def run_command(command: list[str], error_message: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"{command[0]} not found. Install ffmpeg and make sure it is in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() if exc.stderr else str(exc)
        raise SystemExit(f"{error_message}: {details}") from exc


def escape_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    return value
