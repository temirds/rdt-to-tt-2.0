from __future__ import annotations

import textwrap
from pathlib import Path

from .base import VideoScene
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


def _style_line(name: str, font_name: str, font_size: int, config: VideoConfig, bold: int, margin_v: int) -> str:
    return (
        f"Style: {name},{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00101010,&H99000000,"
        f"{bold},0,0,0,100,100,0,0,1,5,1,5,{config.text_margin_h},{config.text_margin_h},{margin_v},1"
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
