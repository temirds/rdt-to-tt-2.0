from __future__ import annotations

import argparse
from pathlib import Path

from .config import CollectorConfig, CollectorQuery, load_collector_settings, merge_config, merge_query
from .service import CollectorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Reddit threads and store them in SQLite.")
    parser.add_argument("--config", default=None, help="Path to collector config.json.")
    parser.add_argument("--db", default=None, help="SQLite database path.")
    parser.add_argument("--subreddit", action="append", dest="subreddits", default=[], help="Subreddit name.")
    parser.add_argument("--keyword", action="append", dest="keywords", default=[], help="Keyword to search for.")
    parser.add_argument("--flair", action="append", dest="flairs", default=[], help="Allowed flair.")
    parser.add_argument("--exclude", action="append", dest="excluded_keywords", default=[], help="Blocked keyword.")
    parser.add_argument("--limit", type=int, default=None, help="Max posts per subreddit query.")
    parser.add_argument("--sort", default=None, choices=["relevance", "hot", "top", "new", "comments"], help="Reddit search sort.")
    parser.add_argument("--timeframe", default=None, choices=["hour", "day", "week", "month", "year", "all"], help="Reddit search timeframe.")
    parser.add_argument("--min-score", type=int, default=None, help="Minimum post score.")
    parser.add_argument("--min-comments", type=int, default=None, help="Minimum number of comments.")
    parser.add_argument("--max-question-length", type=int, default=None, help="Maximum question length in characters.")
    parser.add_argument("--comments-fetch-limit", type=int, default=None, help="How many comments to fetch per post.")
    parser.add_argument("--max-usable-comments", type=int, default=None, help="Maximum number of usable answers to keep.")
    parser.add_argument("--min-usable-comments", type=int, default=None, help="Minimum number of usable answers required to save a post.")
    parser.add_argument("--min-comment-length", type=int, default=None, help="Minimum usable answer length in characters.")
    parser.add_argument("--max-comment-length", type=int, default=None, help="Maximum usable answer length in characters.")
    parser.add_argument("--demo", action="store_true", help="Use built-in demo threads instead of Reddit network requests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loaded = load_collector_settings(Path(args.config) if args.config else Path("collector/config.json"))
    config = merge_config(
        loaded.config,
        db_path=Path(args.db).resolve() if args.db else None,
        comments_fetch_limit=args.comments_fetch_limit,
        max_usable_comments=args.max_usable_comments,
        min_usable_comments=args.min_usable_comments,
        min_comment_length=args.min_comment_length,
        max_comment_length=args.max_comment_length,
    )
    query = merge_query(
        loaded.query,
        subreddits=tuple(args.subreddits) if args.subreddits else None,
        keywords=tuple(args.keywords) if args.keywords else None,
        flair_tags=tuple(args.flairs) if args.flairs else None,
        limit=args.limit,
        sort=args.sort,
        timeframe=args.timeframe,
        min_score=args.min_score,
        min_comments=args.min_comments,
        max_question_length=args.max_question_length,
        excluded_keywords=tuple(args.excluded_keywords) if args.excluded_keywords else None,
    )
    if not args.demo and not query.subreddits:
        raise SystemExit("Pass at least one --subreddit, add subreddits to collector/config.json, or use --demo.")
    service = CollectorService(config=config)
    summary = service.ingest_demo(query) if args.demo else service.ingest(query)
    print(f"Config: {loaded.path}")
    print(f"Database: {config.db_path}")
    print(f"Fetched: {summary.fetched_posts}")
    print(f"Scanned: {summary.scanned_posts}")
    print(f"Prepared: {summary.prepared_posts}")
    print(f"Saved: {summary.saved_posts}")
    print(f"Skipped: {summary.skipped_posts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
