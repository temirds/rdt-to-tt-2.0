from __future__ import annotations

import html
import re
import unicodedata

from .base import RedditPost


URL_RE = re.compile(r"https?://\S+|www\.\S+", flags=re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
REDDIT_NAME_RE = re.compile(r"\b[ur]/[A-Za-z0-9_-]+\b")
CODE_FENCE_RE = re.compile(r"```.*?```", flags=re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`]*)`")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MULTI_SPACE_RE = re.compile(r"[ \t]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
TERMINAL_PUNCTUATION = ".!?…:"

EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2300, 0x23FF),
)
FORMAT_CODEPOINTS = {
    0x200B,  # zero-width space
    0x200C,
    0x200D,  # zero-width joiner
    0x2060,
    0xFE0E,
    0xFE0F,  # emoji variation selector
}


def build_question(post: RedditPost) -> str:
    title = post.title.strip()
    body = post.body.strip()
    if not body:
        return title
    if body.startswith(title):
        return body
    return f"{title}\n\n{body}"


def build_answers(
    post: RedditPost,
    min_comment_length: int,
    max_comment_length: int,
    excluded_keywords: tuple[str, ...] = (),
) -> tuple[str, ...]:
    answers: list[str] = []
    seen_authors: set[str] = set()

    sorted_comments = sorted(
        post.comments,
        key=lambda comment: (comment.score, len(comment.body)),
        reverse=True,
    )
    for comment in sorted_comments:
        body = sanitize_text_for_voiceover(comment.body)
        author = comment.author.strip() or "[deleted]"
        if not body or len(body) < min_comment_length:
            continue
        if len(body) > max_comment_length:
            continue
        lowered = body.lower()
        if any(keyword.lower() in lowered for keyword in excluded_keywords):
            continue
        if author in seen_authors:
            continue
        seen_authors.add(author)
        answers.append(body)

    return tuple(answers)


def render_thread(question: str, answers: tuple[str, ...]) -> str:
    lines = [question.strip(), "", "Ответы:"]
    for index, answer in enumerate(answers, start=1):
        lines.append(f"{index}. {answer}")
    return "\n".join(lines).strip()


def sanitize_question_text(question: str) -> str:
    return sanitize_text_for_voiceover(question)


def sanitize_text_for_voiceover(text: str) -> str:
    cleaned = html.unescape(text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = CODE_FENCE_RE.sub(" ", cleaned)
    cleaned = MARKDOWN_IMAGE_RE.sub(" ", cleaned)
    cleaned = MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = REDDIT_NAME_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"^[>\-*_# \t]+", "", cleaned, flags=re.MULTILINE)
    cleaned = "".join(char for char in cleaned if _is_voiceover_safe_char(char))
    cleaned = MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = MULTI_NEWLINE_RE.sub("\n\n", cleaned)
    return ensure_terminal_punctuation(cleaned.strip())


def ensure_terminal_punctuation(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped[-1] not in TERMINAL_PUNCTUATION:
            stripped = f"{stripped}."
        lines.append(stripped)
    return "\n".join(lines).strip()


def _is_voiceover_safe_char(char: str) -> bool:
    codepoint = ord(char)
    if codepoint in FORMAT_CODEPOINTS:
        return False
    if any(start <= codepoint <= end for start, end in EMOJI_RANGES):
        return False
    if CONTROL_CHARS_RE.match(char):
        return False
    category = unicodedata.category(char)
    if category in {"Cc", "Cf", "Cs", "Co", "Cn"}:
        return char in {"\n", "\t"}
    return True


def question_contains_excluded_keywords(question: str, excluded_keywords: tuple[str, ...]) -> bool:
    lowered = question.lower()
    return any(keyword.lower() in lowered for keyword in excluded_keywords)
