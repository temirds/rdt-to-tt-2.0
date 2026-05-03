from __future__ import annotations

import textwrap

from .base import VideoScene, VideoSegment


MAX_SCENE_CHARS = 220
MIN_SCENE_SECONDS = 1.4


def build_scenes(segments: tuple[VideoSegment, ...], duration_seconds: float) -> tuple[VideoScene, ...]:
    prepared = tuple(segment for segment in segments if segment.text.strip())
    if not prepared:
        raise SystemExit("Video text is empty.")
    if duration_seconds <= 0:
        raise SystemExit("Video duration must be positive.")

    expanded: list[VideoSegment] = []
    for segment in prepared:
        chunks = split_text_for_scenes(segment.text)
        for index, chunk in enumerate(chunks):
            pause = segment.pause_after_seconds if index == len(chunks) - 1 else 0.0
            expanded.append(VideoSegment(text=chunk, pause_after_seconds=pause, role=segment.role))

    weights = [max(24, len(segment.text)) + max(0.0, segment.pause_after_seconds) * 24 for segment in expanded]
    total_weight = sum(weights) or 1
    scenes: list[VideoScene] = []
    cursor = 0.0
    for index, segment in enumerate(expanded):
        if index == len(expanded) - 1:
            end = duration_seconds
        else:
            raw_length = duration_seconds * weights[index] / total_weight
            length = max(MIN_SCENE_SECONDS, raw_length)
            end = min(duration_seconds, cursor + length)
        if end <= cursor:
            break
        scenes.append(
            VideoScene(
                text=segment.text,
                role=segment.role,
                start_seconds=cursor,
                end_seconds=end,
            )
        )
        cursor = end
        if cursor >= duration_seconds:
            break
    return tuple(scenes)


def split_text_for_scenes(text: str) -> tuple[str, ...]:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= MAX_SCENE_CHARS:
        return (cleaned,)

    chunks: list[str] = []
    current = ""
    for sentence in textwrap.wrap(cleaned, width=MAX_SCENE_CHARS, break_long_words=False, break_on_hyphens=False):
        if current and len(current) + 1 + len(sentence) <= MAX_SCENE_CHARS:
            current = f"{current} {sentence}"
            continue
        if current:
            chunks.append(current)
        current = sentence
    if current:
        chunks.append(current)
    return tuple(chunks) or (cleaned[:MAX_SCENE_CHARS],)
