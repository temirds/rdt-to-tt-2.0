from __future__ import annotations

from .base import RedditPost


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
        body = comment.body.strip()
        author = comment.author.strip() or "[deleted]"
        if len(body) < min_comment_length:
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
    return question.strip().replace("\r\n", "\n")


def question_contains_excluded_keywords(question: str, excluded_keywords: tuple[str, ...]) -> bool:
    lowered = question.lower()
    return any(keyword.lower() in lowered for keyword in excluded_keywords)
