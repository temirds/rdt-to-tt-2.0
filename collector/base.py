from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RedditComment:
    external_id: str
    author: str
    body: str
    score: int
    created_utc: int
    permalink: str


@dataclass(frozen=True)
class RedditPost:
    external_id: str
    subreddit: str
    title: str
    body: str
    author: str
    permalink: str
    url: str
    flair: str | None
    score: int
    upvote_ratio: float | None
    comment_count: int
    created_utc: int
    comments: tuple[RedditComment, ...] = ()
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostBatch:
    posts: tuple[RedditPost, ...]
    source_label: str


@dataclass(frozen=True)
class AnalysisResult:
    accepted: bool
    score: float
    reasons: tuple[str, ...]
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class PreparedThread:
    post: RedditPost
    language: str
    analysis: AnalysisResult
    question: str
    answers: tuple[str, ...]
    original_text: str
