from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = MODULE_ROOT / "config.json"


@dataclass(frozen=True)
class CollectorQuery:
    subreddits: tuple[str, ...]
    keywords: tuple[str, ...] = ()
    flair_tags: tuple[str, ...] = ()
    limit: int = 25
    sort: str = "top"
    timeframe: str = "week"
    min_score: int = 50
    min_comments: int = 8
    max_comment_count: int = 40
    min_question_length: int = 25
    min_combined_text_length: int = 80
    excluded_keywords: tuple[str, ...] = ()
    question_excluded_keywords: tuple[str, ...] = ()
    answer_excluded_keywords: tuple[str, ...] = ()
    require_questionish_title: bool = False


@dataclass(frozen=True)
class CollectorConfig:
    db_path: Path = MODULE_ROOT / "data" / "reddit.db"
    user_agent: str = "rdt-to-tt-collector/0.1"
    request_timeout_seconds: int = 20
    reddit_retry_attempts: int = 2
    reddit_retry_delay_seconds: float = 2.0
    shuffle_keywords_each_run: bool = True
    shuffle_seed: int | None = None
    search_batch_size: int = 50
    target_saved_posts_per_run: int = 10
    max_scanned_posts_per_run: int = 500
    progress_every_n_posts: int = 10
    comments_fetch_limit: int = 500
    max_usable_comments: int = 8
    min_usable_comments: int = 5
    min_comment_length: int = 80
    store_raw_payload: bool = True
    duplicate_window_hours: int = 72
    source_name: str = "reddit"
    preferred_answer_count: int = 5
    analysis_min_accepted_score: float = 3.0
    analysis_min_author_diversity: int = 3
    analysis_min_total_answer_chars: int = 320
    analysis_blocked_phrases: tuple[str, ...] = (
        "[removed]",
        "[deleted]",
        "edit:",
        "update:",
        "aita for",
        "tifu by",
        "nsfw",
    )
    english_stopwords: tuple[str, ...] = field(
        default_factory=lambda: (
            "the", "and", "that", "have", "with", "this", "what", "from", "would",
            "there", "their", "about", "because", "people", "really", "should",
        )
    )


@dataclass(frozen=True)
class LoadedCollectorSettings:
    config: CollectorConfig
    query: CollectorQuery
    path: Path


def load_collector_settings(path: Path = DEFAULT_CONFIG_PATH) -> LoadedCollectorSettings:
    path = path.resolve()
    raw = _load_json(path)

    collector_raw = raw.get("collector", {})
    reddit_raw = raw.get("reddit", {})
    filters_raw = raw.get("filters", {})

    config = CollectorConfig(
        db_path=_resolve_path(collector_raw.get("db_path", "data/reddit.db"), path.parent),
        user_agent=str(collector_raw.get("user_agent", CollectorConfig.user_agent)),
        request_timeout_seconds=int(collector_raw.get("request_timeout_seconds", CollectorConfig.request_timeout_seconds)),
        reddit_retry_attempts=int(collector_raw.get("reddit_retry_attempts", CollectorConfig.reddit_retry_attempts)),
        reddit_retry_delay_seconds=float(collector_raw.get("reddit_retry_delay_seconds", CollectorConfig.reddit_retry_delay_seconds)),
        shuffle_keywords_each_run=bool(collector_raw.get("shuffle_keywords_each_run", CollectorConfig.shuffle_keywords_each_run)),
        shuffle_seed=collector_raw.get("shuffle_seed", CollectorConfig.shuffle_seed),
        search_batch_size=int(collector_raw.get("search_batch_size", CollectorConfig.search_batch_size)),
        target_saved_posts_per_run=int(collector_raw.get("target_saved_posts_per_run", CollectorConfig.target_saved_posts_per_run)),
        max_scanned_posts_per_run=int(collector_raw.get("max_scanned_posts_per_run", CollectorConfig.max_scanned_posts_per_run)),
        progress_every_n_posts=int(collector_raw.get("progress_every_n_posts", CollectorConfig.progress_every_n_posts)),
        comments_fetch_limit=int(collector_raw.get("comments_fetch_limit", CollectorConfig.comments_fetch_limit)),
        max_usable_comments=int(collector_raw.get("max_usable_comments", CollectorConfig.max_usable_comments)),
        min_usable_comments=int(collector_raw.get("min_usable_comments", CollectorConfig.min_usable_comments)),
        min_comment_length=int(collector_raw.get("min_comment_length", CollectorConfig.min_comment_length)),
        store_raw_payload=bool(collector_raw.get("store_raw_payload", CollectorConfig.store_raw_payload)),
        duplicate_window_hours=int(collector_raw.get("duplicate_window_hours", CollectorConfig.duplicate_window_hours)),
        source_name=str(collector_raw.get("source_name", CollectorConfig.source_name)),
        preferred_answer_count=int(collector_raw.get("preferred_answer_count", CollectorConfig.preferred_answer_count)),
        analysis_min_accepted_score=float(collector_raw.get("analysis_min_accepted_score", CollectorConfig.analysis_min_accepted_score)),
        analysis_min_author_diversity=int(collector_raw.get("analysis_min_author_diversity", CollectorConfig.analysis_min_author_diversity)),
        analysis_min_total_answer_chars=int(collector_raw.get("analysis_min_total_answer_chars", CollectorConfig.analysis_min_total_answer_chars)),
        analysis_blocked_phrases=_to_tuple(collector_raw.get("analysis_blocked_phrases", CollectorConfig.analysis_blocked_phrases)),
        english_stopwords=_to_tuple(collector_raw.get("english_stopwords", CollectorConfig().english_stopwords)),
    )
    query = CollectorQuery(
        subreddits=_to_tuple(reddit_raw.get("subreddits", ("AskReddit",))),
        keywords=_to_tuple(reddit_raw.get("keywords", ())),
        flair_tags=_to_tuple(reddit_raw.get("flair_tags", ())),
        limit=int(reddit_raw.get("limit", CollectorQuery.limit)),
        sort=str(reddit_raw.get("sort", CollectorQuery.sort)),
        timeframe=str(reddit_raw.get("timeframe", CollectorQuery.timeframe)),
        min_score=int(reddit_raw.get("min_score", CollectorQuery.min_score)),
        min_comments=int(reddit_raw.get("min_comments", CollectorQuery.min_comments)),
        max_comment_count=int(reddit_raw.get("max_comment_count", CollectorQuery.max_comment_count)),
        min_question_length=int(reddit_raw.get("min_question_length", CollectorQuery.min_question_length)),
        min_combined_text_length=int(reddit_raw.get("min_combined_text_length", CollectorQuery.min_combined_text_length)),
        excluded_keywords=_to_tuple(filters_raw.get("excluded_keywords", ())),
        question_excluded_keywords=_to_tuple(filters_raw.get("question_excluded_keywords", ())),
        answer_excluded_keywords=_to_tuple(filters_raw.get("answer_excluded_keywords", ())),
        require_questionish_title=bool(reddit_raw.get("require_questionish_title", CollectorQuery.require_questionish_title)),
    )
    return LoadedCollectorSettings(config=config, query=query, path=path)


def merge_query(base: CollectorQuery, **overrides: Any) -> CollectorQuery:
    cleaned = {key: value for key, value in overrides.items() if value is not None}
    return replace(base, **cleaned)


def merge_config(base: CollectorConfig, **overrides: Any) -> CollectorConfig:
    cleaned = {key: value for key, value in overrides.items() if value is not None}
    return replace(base, **cleaned)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Collector config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _to_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (list, tuple)):
        return tuple(str(value).strip() for value in values if str(value).strip())
    return (str(values).strip(),) if str(values).strip() else ()
