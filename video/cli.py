from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .api import generate_video
from .base import VideoOptions, VideoSegment
from .config import DEFAULT_CONFIG_PATH, load_video_settings


def main() -> int:
    args = parse_args()
    settings = load_video_settings(Path(args.config) if args.config else DEFAULT_CONFIG_PATH)
    config = settings.config
    duration_seconds = float(args.duration)
    if duration_seconds <= 0:
        raise SystemExit("--duration must be positive.")

    silence_path = config.cache_dir / "preview" / f"silence_{int(duration_seconds)}s.wav"
    create_silent_audio(config.ffmpeg_path, silence_path, duration_seconds)

    result = generate_video(
        build_preview_segments(duration_seconds),
        VideoOptions(
            audio_path=silence_path,
            output_path=Path(args.output).resolve(),
            duration_seconds=duration_seconds,
            background_path=Path(args.background).resolve() if args.background else None,
            background_tags=tuple(args.background_tag or ()),
            config_path=Path(args.config).resolve() if args.config else None,
            auto_fetch_background=not args.no_auto_fetch_background,
        ),
    )
    print(f"Video: {result.output_path}")
    print(f"Background: {result.background_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a vertical video preview without voiceover.")
    parser.add_argument("--duration", type=float, default=60.0, help="Preview duration in seconds.")
    parser.add_argument("--output", default="out/video_preview.mp4", help="Output MP4 path.")
    parser.add_argument("--background", help="Local background video path.")
    parser.add_argument(
        "--background-tag",
        action="append",
        help="Background content tag, e.g. minecraft, satisfying, driving. Can be repeated.",
    )
    parser.add_argument("--config", help="Path to video config.json.")
    parser.add_argument(
        "--no-auto-fetch-background",
        action="store_true",
        help="Do not download direct-URL background clips if local videos are missing.",
    )
    return parser.parse_args()


def create_silent_audio(ffmpeg_path: str, output_path: Path, duration_seconds: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t",
        f"{duration_seconds:.3f}",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"{ffmpeg_path} not found. Install ffmpeg and make sure it is in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() if exc.stderr else str(exc)
        raise SystemExit(f"Silent audio generation failed: {details}") from exc


def build_preview_segments(duration_seconds: float) -> tuple[VideoSegment, ...]:
    scene_count = max(1, int(duration_seconds // 8))
    pause = duration_seconds / scene_count
    return tuple(
        VideoSegment(
            text=f"Preview scene {index + 1}",
            pause_after_seconds=pause,
            role="question" if index == 0 else "answer",
        )
        for index in range(scene_count)
    )


if __name__ == "__main__":
    raise SystemExit(main())
