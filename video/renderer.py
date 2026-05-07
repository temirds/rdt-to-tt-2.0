from __future__ import annotations

import json
import random
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
    base_filters = [
        f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase",
        f"crop={config.width}:{config.height}",
        f"fps={config.fps}",
        "eq=brightness=-0.08:contrast=1.05:saturation=0.9",
    ]
    filter_script_path: Path | None = None
    if subtitles_path.suffix.lower() == ".filter":
        drawtext_filter = subtitles_path.read_text(encoding="utf-8").strip()
        filter_graph = ",".join([*base_filters, drawtext_filter] if drawtext_filter else base_filters)
        filter_script_path = output_path.with_suffix(".render.filter")
        filter_script_path.write_text(filter_graph, encoding="utf-8")
        temp_video_path = output_path.with_name(f"_{output_path.stem}_subs.mp4")
        burn_command = [
            config.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(background_path),
            "-an",
            "-filter_script:v",
            str(filter_script_path),
            "-c:v",
            "libx264",
            "-crf",
            str(config.crf),
            "-preset",
            config.preset,
            "-pix_fmt",
            "yuv420p",
            str(temp_video_path),
        ]
        mux_command = [
            config.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(temp_video_path),
            "-i",
            str(audio_path),
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
        try:
            run_command(burn_command, "ffmpeg subtitles failed")
            run_command(mux_command, "ffmpeg mux failed")
            return
        finally:
            for path in (filter_script_path, temp_video_path):
                try:
                    path.unlink()
                except OSError:
                    pass
    else:
        subtitles_filter = f"subtitles='{escape_filter_path(subtitles_path)}'"
        if config.fonts_dir.exists():
            subtitles_filter += f":fontsdir='{escape_filter_path(config.fonts_dir)}'"
        filter_graph = ",".join([*base_filters, subtitles_filter])

    command = [
        config.ffmpeg_path,
        "-y",
        "-i",
        str(background_path),
        "-i",
        str(audio_path),
        "-t",
        f"{duration_seconds:.3f}",
    ]
    if filter_script_path is not None:
        command.extend(["-filter_script:v", str(filter_script_path)])
    else:
        command.extend(["-vf", filter_graph])
    command.extend(
        [
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
    )
    try:
        run_command(command, "ffmpeg render failed")
    finally:
        if filter_script_path is not None:
            try:
                filter_script_path.unlink()
            except OSError:
                pass


def mix_final_audio(
    config: VideoConfig,
    voice_path: Path,
    output_path: Path,
    duration_seconds: float,
    sfx_times: tuple[float, ...] = (),
) -> tuple[Path, Path | None, Path | None]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    music_path = select_random_audio(config.music_dir)
    sfx_path = select_random_audio(config.sfx_dir) if sfx_times else None

    command = [
        config.ffmpeg_path,
        "-y",
        "-i",
        str(voice_path),
        "-f",
        "lavfi",
        "-t",
        f"{duration_seconds:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]
    if music_path is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music_path)])
    if sfx_path is not None:
        for _ in sfx_times:
            command.extend(["-i", str(sfx_path)])

    filter_parts: list[str] = []
    mix_inputs = ["[voice]", "[silence]"]
    filter_parts.append(f"[0:a]atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS[voice]")
    filter_parts.append(f"[1:a]atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS[silence]")

    next_input = 2
    if music_path is not None:
        filter_parts.append(
            f"[{next_input}:a]atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS,"
            f"volume={config.music_volume}[music]"
        )
        mix_inputs.append("[music]")
        next_input += 1

    if sfx_path is not None:
        for index, seconds in enumerate(sfx_times):
            delay_ms = max(0, int(round(seconds * 1000)))
            filter_parts.append(
                f"[{next_input}:a]volume={config.sfx_volume},"
                f"adelay={delay_ms}:all=1,atrim=0:{duration_seconds:.3f}[sfx{index}]"
            )
            mix_inputs.append(f"[sfx{index}]")
            next_input += 1

    filter_parts.append(
        "".join(mix_inputs)
        + f"amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=0,"
        f"atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS[aout]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
    )
    run_command(command, "ffmpeg audio mix failed")
    return output_path, music_path, sfx_path


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


def extract_background_segment(
    config: VideoConfig,
    background_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_graph = ",".join(
        [
            f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase",
            f"crop={config.width}:{config.height}",
            f"fps={config.fps}",
            "setsar=1",
        ]
    )
    command = [
        config.ffmpeg_path,
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(background_path),
        "-t",
        f"{duration_seconds:.3f}",
        "-vf",
        filter_graph,
        "-an",
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
    run_command(command, "ffmpeg background segment extraction failed")
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


def probe_video_duration(config: VideoConfig, video_path: Path) -> float:
    command = [
        config.ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    completed = run_command(command, "ffprobe failed", capture=True)
    payload = json.loads(completed.stdout)
    return float(payload["format"]["duration"])


def select_random_audio(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    candidates = [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in extensions]
    if not candidates:
        return None
    return random.choice(candidates).resolve()


def run_command(command: list[str], error_message: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
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
