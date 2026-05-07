from __future__ import annotations

import textwrap
import json
import re
from pathlib import Path

from .base import VideoScene, VideoSegment
from .config import VideoConfig


def write_ass_subtitles(path: Path, scenes: tuple[VideoScene, ...], config: VideoConfig) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {config.width}",
        f"PlayResY: {config.height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line("Question", config.font_name, config.question_font_size, config, bold=-1, margin_v=config.text_margin_v),
        _style_line("Answer", config.font_name, config.answer_font_size, config, bold=-1, margin_v=config.text_margin_v),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for scene in scenes:
        style = "Question" if scene.role == "question" else "Answer"
        body.append(
            "Dialogue: 0,"
            f"{format_ass_time(scene.start_seconds)},{format_ass_time(scene.end_seconds)},"
            f"{style},,0,0,0,,{escape_ass_text(wrap_scene_text(scene.text, style))}"
        )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def write_word_ass_subtitles(
    path: Path,
    segments: tuple[VideoSegment, ...],
    voice_duration_seconds: float,
    config: VideoConfig,
    timings_path: Path | None = None,
    audio_path: Path | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {config.width}",
        f"PlayResY: {config.height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line(
            "Word",
            config.font_name,
            config.answer_font_size,
            config,
            bold=-1,
            margin_v=0,
            outline=config.subtitle_outline,
            shadow=config.subtitle_shadow,
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    prompt_text = " ".join(segment.text for segment in segments if segment.text.strip())
    timed_segments = load_timed_segments(timings_path)
    timestamp_events = transcribe_word_events(audio_path, config, prompt_text) if config.word_timestamps else ()
    if timed_segments and timestamp_events:
        word_events = build_source_word_events_from_timestamps(timed_segments, timestamp_events)
    elif timed_segments:
        word_events = build_word_events_from_timings(timed_segments, config.word_seconds)
    else:
        word_events = build_word_events(segments, voice_duration_seconds, config.word_seconds)
    for word in word_events:
        body.append(
            "Dialogue: 0,"
            f"{format_ass_time(word.start_seconds)},{format_ass_time(word.end_seconds)},"
            "Word,,0,0,0,,"
            r"{\an5\fscx95\fscy95\t(0,90,\fscx102\fscy102)\t(90,180,\fscx100\fscy100)}"
            f"{escape_ass_text(word.text.upper())}"
        )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def write_drawtext_subtitle_filter(
    path: Path,
    segments: tuple[VideoSegment, ...],
    audio_path: Path,
    duration_seconds: float,
    config: VideoConfig,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = " ".join(segment.text for segment in segments if segment.text.strip())
    filter_graph = None
    if config.word_timestamps:
        timings = transcribe_drawtext_words(audio_path, config)
        if timings:
            filter_graph = build_drawtext_filter_from_words(timings, config)
    if not filter_graph:
        filter_graph = build_drawtext_filter(text, duration_seconds, config)
    if not filter_graph:
        path.write_text("null", encoding="utf-8")
    else:
        path.write_text(filter_graph, encoding="utf-8")
    return path


def build_drawtext_filter(text: str, duration: float, config: VideoConfig) -> str | None:
    words = split_drawtext_words(text)
    if not words or duration <= 0:
        return None
    weights = [max(1, len(word)) for word in words]
    total_weight = float(sum(weights))
    start = 0.0
    items: list[tuple[str, float, float]] = []
    for index, (word, weight) in enumerate(zip(words, weights), start=1):
        end = duration if index == len(words) else start + duration * (weight / total_weight)
        items.append((word, start, end))
        start = end
    return build_drawtext_filter_from_words(items, config)


def build_drawtext_filter_from_words(
    words: list[tuple[str, float, float]] | tuple[tuple[str, float, float], ...],
    config: VideoConfig,
) -> str | None:
    filters: list[str] = []
    font_path = find_font_path(config)
    font_file = escape_filter_path(font_path)
    anim_scale_start = 0.85
    anim_duration = 0.08
    prepared_words = clamp_overlapping_words(
        [(word, start, end) for word, start, end in words if end > start]
    )
    for word, start, end in prepared_words:
        if not word or end <= start:
            continue
        if end - start < 0.12:
            end = start + 0.12
        escaped = escape_drawtext(word)
        size_expr = (
            f"if(lt(t\\,{start + anim_duration:.3f})\\,"
            f"{config.answer_font_size}*({anim_scale_start:.2f}+(1-{anim_scale_start:.2f})"
            f"*(t-{start:.3f})/{anim_duration:.3f})\\,"
            f"{config.answer_font_size})"
        )
        filters.append(
            "drawtext="
            f"fontfile='{font_file}':"
            f"text='{escaped}':"
            f"fontsize={size_expr}:"
            "fontcolor=white:"
            f"borderw={config.subtitle_outline}:"
            "bordercolor=black:"
            "x=(w-text_w)/2:"
            "y=(h-text_h)/2:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        )
    return ",".join(filters) if filters else None


def transcribe_drawtext_words(audio_path: Path, config: VideoConfig) -> list[tuple[str, float, float]]:
    faster_words = transcribe_drawtext_words_faster_whisper(audio_path, config)
    if faster_words:
        return faster_words
    return transcribe_drawtext_words_openai_whisper(audio_path, config)


def transcribe_drawtext_words_faster_whisper(audio_path: Path, config: VideoConfig) -> list[tuple[str, float, float]]:
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return []
    try:
        model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(
            str(audio_path),
            language=config.whisper_language or None,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 200},
            beam_size=5,
        )
        words: list[tuple[str, float, float]] = []
        for segment in segments:
            for item in segment.words or []:
                word = normalize_drawtext_word(item.word or "")
                if word:
                    words.append((word, float(item.start), float(item.end)))
        return clamp_overlapping_words(words)
    except Exception:
        return []


def transcribe_drawtext_words_openai_whisper(audio_path: Path, config: VideoConfig) -> list[tuple[str, float, float]]:
    try:
        import whisper
    except Exception:
        return []
    try:
        model = whisper.load_model(config.whisper_model)
        result = model.transcribe(
            str(audio_path),
            language=config.whisper_language or None,
            word_timestamps=True,
            fp16=False,
            verbose=False,
        )
    except Exception:
        return []
    words: list[tuple[str, float, float]] = []
    for segment in result.get("segments") or ():
        for item in segment.get("words") or ():
            word = normalize_drawtext_word(str(item.get("word") or ""))
            if not word:
                continue
            try:
                words.append((word, float(item.get("start")), float(item.get("end"))))
            except Exception:
                continue
    return clamp_overlapping_words(words)


def clamp_overlapping_words(words: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    words = sorted(words, key=lambda item: item[1])
    result: list[tuple[str, float, float]] = []
    for index, (word, start, end) in enumerate(words):
        start = max(0.0, start)
        end = max(start + 0.05, end)
        if index + 1 < len(words):
            next_start = max(0.0, words[index + 1][1])
            end = min(end, max(start + 0.05, next_start - 0.001))
        result.append((word, start, end))
    return result


def split_drawtext_words(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text.replace("+", ""))
    return [normalize_drawtext_word(word) for word in words if normalize_drawtext_word(word)]


def normalize_drawtext_word(word: str) -> str:
    text = re.sub(r"[^A-Za-zА-Яа-яЁё0-9]+", "", word or "")
    return text.upper() if text else ""


def escape_drawtext(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")
    value = value.replace("%", "\\%")
    value = value.replace("\n", " ")
    return value


def escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace("\\", "\\\\")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    return value


def find_font_path(config: VideoConfig) -> Path:
    preferred = config.fonts_dir / "Exo2_Black.ttf"
    if preferred.exists():
        return preferred.resolve()
    candidates = list(config.fonts_dir.glob("*.ttf")) if config.fonts_dir.exists() else []
    if candidates:
        return candidates[0].resolve()
    return Path("arial.ttf")


class WordEvent:
    def __init__(self, text: str, start_seconds: float, end_seconds: float) -> None:
        self.text = text
        self.start_seconds = start_seconds
        self.end_seconds = end_seconds


class TimedSegment:
    def __init__(self, text: str, start_seconds: float, end_seconds: float) -> None:
        self.text = text
        self.start_seconds = start_seconds
        self.end_seconds = end_seconds


def load_timed_segments(path: Path | None) -> tuple[TimedSegment, ...]:
    if path is None or not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    segments = []
    for item in payload.get("segments") or ():
        try:
            text = str(item.get("text") or "").strip()
            start = float(item.get("start_seconds"))
            end = float(item.get("end_seconds"))
        except Exception:
            continue
        if text and end > start:
            segments.append(TimedSegment(text, start, end))
    return tuple(segments)


def build_word_events_from_timings(
    segments: tuple[TimedSegment, ...],
    preferred_word_seconds: float,
) -> tuple[WordEvent, ...]:
    events: list[WordEvent] = []
    for segment in segments:
        words = _words(segment.text)
        if not words:
            continue
        span = segment.end_seconds - segment.start_seconds
        step = span / len(words)
        word_duration = min(max(0.18, preferred_word_seconds), max(0.18, step))
        for index, word in enumerate(words):
            start = segment.start_seconds + index * step
            end = min(segment.end_seconds, start + word_duration)
            if end > start:
                events.append(WordEvent(word, start, end))
    return tuple(events)


def build_source_word_events_from_timestamps(
    source_segments: tuple[TimedSegment, ...],
    timestamp_events: tuple[WordEvent, ...],
) -> tuple[WordEvent, ...]:
    source_words = [word for segment in source_segments for word in _words(segment.text)]
    timings = sorted(timestamp_events, key=lambda item: item.start_seconds)
    limit = min(len(source_words), len(timings))
    events: list[WordEvent] = []
    for index in range(limit):
        timing = timings[index]
        start = max(0.0, timing.start_seconds)
        end = max(start + 0.05, timing.end_seconds)
        if index + 1 < limit:
            next_start = max(0.0, timings[index + 1].start_seconds)
            end = min(end, max(start + 0.05, next_start - 0.001))
        events.append(WordEvent(source_words[index], start, end))
    return tuple(events)


def transcribe_word_events(audio_path: Path | None, config: VideoConfig, prompt_text: str = "") -> tuple[WordEvent, ...]:
    if audio_path is None or not audio_path.exists():
        return ()
    try:
        import whisper
    except Exception:
        return ()
    try:
        model = whisper.load_model(config.whisper_model)
        result = model.transcribe(
            str(audio_path),
            language=config.whisper_language or None,
            initial_prompt=prompt_text[:800] or None,
            word_timestamps=True,
            fp16=False,
            verbose=False,
            condition_on_previous_text=False,
        )
    except Exception:
        return ()

    events: list[WordEvent] = []
    for segment in result.get("segments") or ():
        for item in segment.get("words") or ():
            text = str(item.get("word") or "").strip()
            text = re.sub(r"^[^\wА-Яа-яЁё]+|[^\wА-Яа-яЁё]+$", "", text, flags=re.UNICODE)
            if not text:
                continue
            try:
                start = float(item.get("start"))
                end = float(item.get("end"))
            except Exception:
                continue
            if end > start:
                events.append(WordEvent(text, start, end))
    return tuple(events)


def build_word_events(
    segments: tuple[VideoSegment, ...],
    voice_duration_seconds: float,
    preferred_word_seconds: float,
) -> tuple[WordEvent, ...]:
    prepared = tuple(segment for segment in segments if segment.text.strip())
    if not prepared:
        return ()

    total_pause = sum(max(0.0, segment.pause_after_seconds) for segment in prepared)
    speech_duration = max(0.001, voice_duration_seconds - total_pause)
    weights = [max(1, len(_words(segment.text))) for segment in prepared]
    total_weight = sum(weights) or 1

    events: list[WordEvent] = []
    cursor = 0.0
    for index, segment in enumerate(prepared):
        words = _words(segment.text)
        segment_speech = speech_duration * weights[index] / total_weight
        if index == len(prepared) - 1:
            segment_end = max(cursor, voice_duration_seconds - max(0.0, segment.pause_after_seconds))
        else:
            segment_end = cursor + segment_speech
        if words and segment_end > cursor:
            word_duration = min(max(0.18, preferred_word_seconds), max(0.18, (segment_end - cursor) / len(words)))
            step = (segment_end - cursor) / len(words)
            for word_index, word in enumerate(words):
                start = cursor + word_index * step
                end = min(segment_end, start + word_duration)
                if end > start:
                    events.append(WordEvent(word, start, end))
        cursor = max(cursor, segment_end) + max(0.0, segment.pause_after_seconds)
    return tuple(events)


def _words(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in re.finditer(r"[\wА-Яа-яЁё]+", text, flags=re.UNICODE))


def _style_line(
    name: str,
    font_name: str,
    font_size: int,
    config: VideoConfig,
    bold: int,
    margin_v: int,
    outline: int = 5,
    shadow: int = 1,
) -> str:
    return (
        f"Style: {name},{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00101010,&H99000000,"
        f"{bold},0,0,0,100,100,0,0,1,{outline},{shadow},5,{config.text_margin_h},{config.text_margin_h},{margin_v},1"
    )


def wrap_scene_text(text: str, style: str) -> str:
    width = 28 if style == "Question" else 25
    return "\\N".join(textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False))


def escape_ass_text(text: str) -> str:
    return text.replace("{", r"\{").replace("}", r"\}")


def format_ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds >= 100:
        whole_seconds += 1
        centiseconds = 0
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"
