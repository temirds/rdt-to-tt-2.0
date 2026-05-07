from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from collector.config import load_collector_settings
from translator import TranslatorService, load_translation_settings
from video import VideoOptions, VideoSegment, generate_video
from video.config import DEFAULT_CONFIG_PATH as DEFAULT_VIDEO_CONFIG_PATH, load_video_settings
from voiceover import VoiceoverOptions, VoiceoverSegment, generate_voiceover, generate_voiceover_sequence


ROOT_PATH = Path(__file__).resolve().parent
QUESTION_PAUSE_SECONDS = 1.0
ANSWER_PAUSE_SECONDS = 0.5


@dataclass(frozen=True)
class SourceSegment:
    text: str
    pause_after_seconds: float = 0.0


@dataclass(frozen=True)
class PipelineSource:
    text: str
    source_language: str
    segments: tuple[SourceSegment, ...] = ()
    thread_id: int | None = None
    title: str | None = None
    external_id: str | None = None
    db_path: Path | None = None


def main() -> int:
    args = parse_args()
    source = resolve_source(args)
    log_message("Источник выбран")
    translated_segments = translate_segments(source, args)
    translated_text = render_translated_text(translated_segments)
    text_output_path = resolve_text_output_path(args, source)
    if text_output_path is not None:
        save_text(text_output_path, translated_text)
        log_message(f"Текст сохранён: {text_output_path}")

    print_source_summary(source, text_output_path)
    if args.translate_only:
        print()
        print(translated_text)
        return 0

    output_path = resolve_audio_output_path(args, source)
    log_message("Озвучка началась")
    voiceover_started_at = time.perf_counter()
    result = synthesize_translated_segments(translated_segments, args, output_path)
    ensure_voiceover_timings(result.output_path, translated_segments, result.duration_seconds)
    result = add_sfx_to_voiceover(result, translated_segments, args)
    log_message_with_duration("Озвучка закончилась", voiceover_started_at)

    print(f"Audio: {result.output_path}")
    print(f"Duration: {result.duration_seconds:.2f} sec")
    if args.video:
        video_output_path = resolve_video_output_path(args, source, result.output_path)
        log_message("Сборка видео началась")
        video_started_at = time.perf_counter()
        video_result = generate_video(
            build_video_segments(translated_segments),
            VideoOptions(
                audio_path=result.output_path,
                output_path=video_output_path,
                duration_seconds=result.duration_seconds,
                background_path=Path(args.background).resolve() if args.background else None,
                background_tags=tuple(args.background_tag or ()),
                config_path=Path(args.video_config).resolve() if args.video_config else None,
                auto_fetch_background=not args.no_auto_fetch_background,
            ),
        )
        log_message_with_duration("Сборка видео закончилась", video_started_at)
        print(f"Video: {video_result.output_path}")
        print(f"Background: {video_result.background_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate Reddit threads from SQLite and synthesize Russian voiceover.")
    parser.add_argument("--voice", default="upvote_3", help="Voice name from voiceover voices config.")
    parser.add_argument("--pitch", type=float, default=None, help="Pitch shift in semitones, from -12 to 12.")
    parser.add_argument("--speed", type=float, default=None, help="Speech speed multiplier, from 0.5 to 2.0.")
    parser.add_argument("--text", help="Source text to translate and synthesize.")
    parser.add_argument("--file", help="UTF-8 text file to translate and synthesize.")
    parser.add_argument("--source-language", default=None, help="Source language for --text/--file, e.g. en or ru.")
    parser.add_argument("--thread-id", type=int, help="Collector thread id from SQLite.")
    parser.add_argument("--db", help="Collector SQLite path. Defaults to collector/config.json.")
    parser.add_argument("--latest-thread", action="store_true", help="Use the latest thread from collector DB.")
    parser.add_argument("--translate-only", action="store_true", help="Only translate and print/save text, skip voiceover.")
    parser.add_argument("--translator-config", default=None, help="Path to translator config.json.")
    parser.add_argument("--translated-text-output", help="Path to save the translated UTF-8 text.")
    parser.add_argument("-o", "--output", help="Output WAV path.")
    parser.add_argument("--video", action="store_true", help="Render vertical TikTok MP4 after voiceover.")
    parser.add_argument("--video-output", help="Output MP4 path.")
    parser.add_argument("--background", help="Local background video path for MP4 rendering.")
    parser.add_argument(
        "--background-tag",
        action="append",
        help="Background content tag, e.g. minecraft, satisfying, driving. Can be repeated.",
    )
    parser.add_argument("--video-config", help="Path to video config.json.")
    parser.add_argument(
        "--no-auto-fetch-background",
        action="store_true",
        help="Do not download direct-URL background clips if local videos are missing.",
    )
    return parser.parse_args()


def resolve_source(args: argparse.Namespace) -> PipelineSource:
    provided_sources = sum(
        1
        for value in (
            bool(args.text),
            bool(args.file),
            bool(args.thread_id),
            bool(args.latest_thread),
        )
        if value
    )
    if provided_sources > 1:
        raise SystemExit("Use only one source: --text, --file, --thread-id, or --latest-thread.")

    if args.text:
        return build_text_source(
            text=clean_text(args.text),
            source_language=(args.source_language or "en").strip().lower(),
            title="manual_text",
        )

    if args.file:
        return build_text_source(
            text=clean_text(Path(args.file).read_text(encoding="utf-8")),
            source_language=(args.source_language or "en").strip().lower(),
            title=Path(args.file).stem,
        )

    if args.thread_id is not None or args.latest_thread:
        return load_thread_from_db(
            db_path=resolve_db_path(args.db),
            thread_id=args.thread_id,
            use_latest=True,
        )

    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read()
        if stdin_text.strip():
            return build_text_source(
                text=clean_text(stdin_text),
                source_language=(args.source_language or "en").strip().lower(),
                title="stdin",
            )

    return load_thread_from_db(
        db_path=resolve_db_path(args.db),
        thread_id=None,
        use_latest=True,
    )


def build_text_source(text: str, source_language: str, title: str) -> PipelineSource:
    segments = (SourceSegment(text=text, pause_after_seconds=0.0),)
    return PipelineSource(
        text=text,
        source_language=source_language,
        segments=segments,
        title=title,
    )


def resolve_db_path(db_override: str | None) -> Path:
    if db_override:
        path = Path(db_override)
        return path if path.is_absolute() else (ROOT_PATH / path).resolve()
    loaded = load_collector_settings(ROOT_PATH / "collector" / "config.json")
    return loaded.config.db_path


def load_thread_from_db(db_path: Path, thread_id: int | None, use_latest: bool) -> PipelineSource:
    query = (
        "SELECT id, external_id, title, question, answers_json, language FROM collector_threads WHERE id = ?"
        if thread_id is not None
        else "SELECT id, external_id, title, question, answers_json, language FROM collector_threads ORDER BY id DESC LIMIT 1"
    )
    params: tuple[object, ...] = (thread_id,) if thread_id is not None else ()

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(query, params).fetchone()
    finally:
        connection.close()

    if row is None:
        if thread_id is not None:
            raise SystemExit(f"Collector thread id {thread_id} not found in {db_path}.")
        if use_latest:
            raise SystemExit(f"No threads found in {db_path}.")
        raise SystemExit("No input source resolved.")

    question = clean_text(str(row["question"]))
    raw_answers = json.loads(str(row["answers_json"]))
    answers = tuple(clean_text(str(answer)) for answer in raw_answers if str(answer).strip())
    segments = build_thread_segments(question, answers)

    return PipelineSource(
        text=render_source_text(segments),
        source_language=str(row["language"]).strip().lower() or "en",
        segments=segments,
        thread_id=int(row["id"]),
        title=str(row["title"]).strip(),
        external_id=str(row["external_id"]).strip(),
        db_path=db_path,
    )


def build_thread_segments(question: str, answers: tuple[str, ...]) -> tuple[SourceSegment, ...]:
    segments: list[SourceSegment] = [SourceSegment(text=question, pause_after_seconds=QUESTION_PAUSE_SECONDS)]
    for index, answer in enumerate(answers):
        pause_after_seconds = ANSWER_PAUSE_SECONDS if index < len(answers) - 1 else 0.0
        segments.append(SourceSegment(text=answer, pause_after_seconds=pause_after_seconds))
    return tuple(segments)


def translate_segments(source: PipelineSource, args: argparse.Namespace) -> tuple[SourceSegment, ...]:
    if source.source_language in {"ru", "rus", "russian"}:
        log_message("Перевод пропущен: исходный текст уже на русском")
        return source.segments or (SourceSegment(text=source.text, pause_after_seconds=0.0),)

    loaded = load_translation_settings(
        Path(args.translator_config) if args.translator_config else ROOT_PATH / "translator" / "config.json"
    )
    service = TranslatorService(config=loaded.config)
    log_message("Перевод начался")
    translation_started_at = time.perf_counter()
    translated_segments = tuple(
        SourceSegment(
            text=clean_text(service.translate(segment.text, source_language=source.source_language, target_language="ru")),
            pause_after_seconds=segment.pause_after_seconds,
        )
        for segment in source.segments
    )
    log_message_with_duration("Перевод закончился", translation_started_at)
    return translated_segments


def render_source_text(segments: tuple[SourceSegment, ...]) -> str:
    return "\n\n".join(segment.text for segment in segments if segment.text.strip())


def render_translated_text(segments: tuple[SourceSegment, ...]) -> str:
    return render_source_text(segments)


def synthesize_translated_segments(
    translated_segments: tuple[SourceSegment, ...],
    args: argparse.Namespace,
    output_path: Path,
):
    voice_options = VoiceoverOptions(
        voice=args.voice,
        pitch=args.pitch,
        speed=args.speed,
        output_path=output_path,
    )

    if len(translated_segments) == 1 and translated_segments[0].pause_after_seconds <= 0:
        return generate_voiceover(translated_segments[0].text, voice_options)

    return generate_voiceover_sequence(
        [
            VoiceoverSegment(
                text=segment.text,
                pause_after_seconds=segment.pause_after_seconds,
            )
            for segment in translated_segments
        ],
        voice_options,
    )


def add_sfx_to_voiceover(result, translated_segments: tuple[SourceSegment, ...], args: argparse.Namespace):
    sfx_times = build_sfx_times_from_timings(result.output_path) or build_sfx_times_for_segments(
        translated_segments,
        result.duration_seconds,
    )
    if not sfx_times:
        return result

    settings = load_video_settings(Path(args.video_config) if args.video_config else DEFAULT_VIDEO_CONFIG_PATH)
    sfx_path = select_random_audio(settings.config.sfx_dir)
    if sfx_path is None:
        return result

    log_message(f"Озвучка: добавляем SFX {sfx_path.name} между комментариями, точек {len(sfx_times)}")
    output_path = Path(result.output_path).resolve()
    temp_path = output_path.with_name(f"{output_path.stem}_with_sfx{output_path.suffix}")
    command = [
        settings.config.ffmpeg_path,
        "-y",
        "-i",
        str(output_path),
    ]
    for _ in sfx_times:
        command.extend(["-i", str(sfx_path)])

    filter_parts = ["[0:a]asetpts=PTS-STARTPTS[voice]"]
    mix_inputs = ["[voice]"]
    for index, seconds in enumerate(sfx_times):
        delay_ms = max(0, int(round(seconds * 1000)))
        filter_parts.append(
            f"[{index + 1}:a]volume={settings.config.sfx_volume},"
            f"adelay={delay_ms}:all=1[sfx{index}]"
        )
        mix_inputs.append(f"[sfx{index}]")
    filter_parts.append(
        "".join(mix_inputs)
        + f"amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=0[aout]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[aout]",
            "-c:a",
            "pcm_s16le",
            str(temp_path),
        ]
    )
    run_command(command, "SFX mix failed")
    temp_path.replace(output_path)
    return replace(
        result,
        duration_seconds=probe_audio_duration(settings.config.ffprobe_path, output_path),
    )


def ensure_voiceover_timings(
    audio_path: Path,
    segments: tuple[SourceSegment, ...],
    duration_seconds: float,
) -> None:
    timings_path = Path(audio_path).with_suffix(".timings.json")
    if timings_path.exists():
        return
    prepared = tuple(segment for segment in segments if segment.text.strip())
    if not prepared:
        return
    total_pause = sum(max(0.0, segment.pause_after_seconds) for segment in prepared)
    speech_duration = max(0.001, duration_seconds - total_pause)
    weights = [max(1, len(segment.text.split())) for segment in prepared]
    total_weight = sum(weights) or 1
    cursor = 0.0
    payload_segments = []
    for index, segment in enumerate(prepared):
        segment_speech = speech_duration * weights[index] / total_weight
        if index == len(prepared) - 1:
            segment_end = max(cursor, duration_seconds - max(0.0, segment.pause_after_seconds))
        else:
            segment_end = cursor + segment_speech
        payload_segments.append(
            {
                "text": segment.text,
                "start_seconds": cursor,
                "end_seconds": segment_end,
                "pause_after_seconds": max(0.0, segment.pause_after_seconds),
            }
        )
        cursor = segment_end + max(0.0, segment.pause_after_seconds)
    timings_path.write_text(
        json.dumps({"segments": payload_segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_sfx_times_for_segments(segments: tuple[SourceSegment, ...], duration_seconds: float) -> tuple[float, ...]:
    # Prefer exact word/chunk timings saved by the TTS sequence. The caller passes source
    # segments, so this fallback is used only when no sidecar timing file exists.
    prepared = tuple(segment for segment in segments if segment.text.strip())
    if len(prepared) <= 1:
        return ()
    total_pause = sum(max(0.0, segment.pause_after_seconds) for segment in prepared)
    speech_duration = max(0.001, duration_seconds - total_pause)
    weights = [max(1, len(segment.text.split())) for segment in prepared]
    total_weight = sum(weights) or 1
    times: list[float] = []
    cursor = 0.0
    for index, segment in enumerate(prepared[:-1]):
        cursor += speech_duration * weights[index] / total_weight
        if segment.pause_after_seconds > 0:
            times.append(min(max(0.0, cursor), max(0.0, duration_seconds - 0.05)))
        cursor += max(0.0, segment.pause_after_seconds)
    return tuple(times)


def build_sfx_times_from_timings(audio_path: Path) -> tuple[float, ...]:
    timings_path = Path(audio_path).with_suffix(".timings.json")
    if not timings_path.exists():
        return ()
    try:
        payload = json.loads(timings_path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    times: list[float] = []
    for item in payload.get("segments") or ():
        try:
            pause = float(item.get("pause_after_seconds") or 0.0)
            end = float(item.get("end_seconds"))
        except Exception:
            continue
        if pause > 0:
            times.append(max(0.0, end))
    return tuple(times)


def select_random_audio(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    candidates = [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in extensions]
    return random.choice(candidates).resolve() if candidates else None


def probe_audio_duration(ffprobe_path: str, audio_path: Path) -> float:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(audio_path),
    ]
    completed = run_command(command, "ffprobe failed", capture=True)
    return float(json.loads(completed.stdout)["format"]["duration"])


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


def resolve_text_output_path(args: argparse.Namespace, source: PipelineSource) -> Path | None:
    if args.translated_text_output:
        path = Path(args.translated_text_output)
        return path if path.is_absolute() else (ROOT_PATH / path).resolve()

    if source.thread_id is None and not args.translate_only:
        return None

    output_dir = ROOT_PATH / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = build_output_stem(source)
    return output_dir / f"{stem}.txt"


def resolve_audio_output_path(args: argparse.Namespace, source: PipelineSource) -> Path:
    if args.output:
        output_path = Path(args.output)
        return output_path if output_path.is_absolute() else (ROOT_PATH / output_path).resolve()

    output_dir = ROOT_PATH / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{build_output_stem(source, max_slug_chars=40)}_{timestamp}.wav"


def resolve_video_output_path(args: argparse.Namespace, source: PipelineSource, audio_output_path: Path) -> Path:
    if args.video_output:
        output_path = Path(args.video_output)
        return output_path if output_path.is_absolute() else (ROOT_PATH / output_path).resolve()

    if args.output:
        return audio_output_path.with_suffix(".mp4")

    output_dir = ROOT_PATH / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{build_output_stem(source, max_slug_chars=40)}_{timestamp}.mp4"


def build_video_segments(translated_segments: tuple[SourceSegment, ...]) -> tuple[VideoSegment, ...]:
    return tuple(
        VideoSegment(
            text=segment.text,
            pause_after_seconds=segment.pause_after_seconds,
            role="question" if index == 0 and len(translated_segments) > 1 else "answer",
        )
        for index, segment in enumerate(translated_segments)
    )


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def print_source_summary(source: PipelineSource, text_output_path: Path | None) -> None:
    if source.thread_id is not None:
        print(f"Thread id: {source.thread_id}")
        print(f"Title: {source.title}")
        print(f"External id: {source.external_id}")
        print(f"Database: {source.db_path}")
        print(f"Segments: {len(source.segments)}")
    else:
        print(f"Source language: {source.source_language}")

    if text_output_path is not None:
        print(f"Translated text: {text_output_path}")


def log_message(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def log_message_with_duration(message: str, started_at: float) -> None:
    duration_seconds = time.perf_counter() - started_at
    log_message(f"{message} ({duration_seconds:.1f} сек)")


def build_output_stem(source: PipelineSource, max_slug_chars: int = 80) -> str:
    title = source.title or source.external_id or "thread"
    slug = slugify(title, max_chars=max_slug_chars)
    if source.thread_id is not None:
        return f"thread_{source.thread_id}_{slug}"
    return slug


def slugify(value: str, max_chars: int = 80) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "_", value.strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:max_chars] or "output"


def clean_text(text: str) -> str:
    text = text.strip().replace("\r\n", "\n")
    if not text:
        raise SystemExit("Text is empty.")
    return text


if __name__ == "__main__":
    raise SystemExit(main())
