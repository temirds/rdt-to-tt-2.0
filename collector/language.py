from __future__ import annotations

import re


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
LATIN_RE = re.compile(r"[A-Za-z]")
WORD_RE = re.compile(r"[A-Za-z']+")


def detect_language(text: str, english_stopwords: tuple[str, ...]) -> str:
    content = text.strip()
    if not content:
        return "unknown"

    cyrillic_count = len(CYRILLIC_RE.findall(content))
    latin_count = len(LATIN_RE.findall(content))
    if cyrillic_count > latin_count * 1.3:
        return "ru"

    words = [word.lower() for word in WORD_RE.findall(content)]
    if not words:
        return "unknown"

    english_hits = sum(1 for word in words if word in english_stopwords)
    if latin_count > cyrillic_count and english_hits >= 2:
        return "en"

    if latin_count > cyrillic_count * 2:
        return "en"
    if cyrillic_count > 0:
        return "ru"
    return "unknown"

