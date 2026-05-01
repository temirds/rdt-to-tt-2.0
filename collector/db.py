from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .base import PreparedThread


THREADS_TABLE = "collector_threads"
COMMENTS_TABLE = "collector_comments"
LEGACY_THREADS_TABLE = "harvested_threads"
LEGACY_COMMENTS_TABLE = "harvested_comments"


SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    subreddit TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    question TEXT NOT NULL,
    answers_json TEXT NOT NULL,
    original_text TEXT NOT NULL,
    language TEXT NOT NULL,
    flair TEXT,
    author TEXT NOT NULL,
    permalink TEXT NOT NULL,
    url TEXT NOT NULL,
    score INTEGER NOT NULL,
    upvote_ratio REAL,
    comment_count INTEGER NOT NULL,
    analysis_score REAL NOT NULL,
    analysis_reasons_json TEXT NOT NULL,
    matched_keywords_json TEXT NOT NULL,
    created_utc INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    raw_payload_json TEXT
);

CREATE TABLE IF NOT EXISTS collector_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES collector_threads(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    score INTEGER NOT NULL,
    created_utc INTEGER NOT NULL,
    permalink TEXT NOT NULL,
    UNIQUE(thread_id, external_id)
);
"""


@dataclass(frozen=True)
class SavedThread:
    row_id: int
    inserted: bool
    external_id: str
    title: str
    original_text: str


class CollectorDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            self._migrate_legacy_schema(connection)
            connection.executescript(SCHEMA)
            self._cleanup_legacy_tables(connection)

    def has_thread(self, external_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM {THREADS_TABLE} WHERE external_id = ?",
                (external_id,),
            ).fetchone()
        return row is not None

    def save_thread(self, source_name: str, prepared: PreparedThread, store_raw_payload: bool) -> SavedThread:
        if self.has_thread(prepared.post.external_id):
            with self.connect() as connection:
                row = connection.execute(
                    f"SELECT id FROM {THREADS_TABLE} WHERE external_id = ?",
                    (prepared.post.external_id,),
                ).fetchone()
            return SavedThread(
                row_id=int(row["id"]),
                inserted=False,
                external_id=prepared.post.external_id,
                title=prepared.post.title,
                original_text=prepared.original_text,
            )

        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO collector_threads (
                    source_name, external_id, subreddit, title, body, question, answers_json,
                    original_text, language, flair, author, permalink, url,
                    score, upvote_ratio, comment_count, analysis_score, analysis_reasons_json,
                    matched_keywords_json, created_utc, ingested_at, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    prepared.post.external_id,
                    prepared.post.subreddit,
                    prepared.post.title,
                    prepared.post.body,
                    prepared.question,
                    json.dumps(prepared.answers, ensure_ascii=False),
                    prepared.original_text,
                    prepared.language,
                    prepared.post.flair,
                    prepared.post.author,
                    prepared.post.permalink,
                    prepared.post.url,
                    prepared.post.score,
                    prepared.post.upvote_ratio,
                    prepared.post.comment_count,
                    prepared.analysis.score,
                    json.dumps(prepared.analysis.reasons, ensure_ascii=False),
                    json.dumps(prepared.analysis.matched_keywords, ensure_ascii=False),
                    prepared.post.created_utc,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(prepared.post.raw_payload, ensure_ascii=False) if store_raw_payload else None,
                ),
            )
            thread_id = int(cursor.lastrowid)
            for comment in prepared.post.comments:
                connection.execute(
                    """
                    INSERT INTO collector_comments (
                        thread_id, external_id, author, body, score, created_utc, permalink
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        comment.external_id,
                        comment.author,
                        comment.body,
                        comment.score,
                        comment.created_utc,
                        comment.permalink,
                    ),
                )
        return SavedThread(
            row_id=thread_id,
            inserted=True,
            external_id=prepared.post.external_id,
            title=prepared.post.title,
            original_text=prepared.original_text,
        )

    def _migrate_legacy_schema(self, connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if LEGACY_THREADS_TABLE not in tables:
            return
        if THREADS_TABLE in tables:
            return

        connection.executescript(
            f"""
            CREATE TABLE {THREADS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                external_id TEXT NOT NULL UNIQUE,
                subreddit TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                question TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                original_text TEXT NOT NULL,
                language TEXT NOT NULL,
                flair TEXT,
                author TEXT NOT NULL,
                permalink TEXT NOT NULL,
                url TEXT NOT NULL,
                score INTEGER NOT NULL,
                upvote_ratio REAL,
                comment_count INTEGER NOT NULL,
                analysis_score REAL NOT NULL,
                analysis_reasons_json TEXT NOT NULL,
                matched_keywords_json TEXT NOT NULL,
                created_utc INTEGER NOT NULL,
                ingested_at TEXT NOT NULL,
                raw_payload_json TEXT
            );

            INSERT INTO {THREADS_TABLE} (
                id, source_name, external_id, subreddit, title, body, question, answers_json,
                original_text, language, flair, author, permalink, url, score, upvote_ratio,
                comment_count, analysis_score, analysis_reasons_json, matched_keywords_json,
                created_utc, ingested_at, raw_payload_json
            )
            SELECT
                id, source_name, external_id, subreddit, title, body, question, answers_json,
                original_text, language, flair, author, permalink, url, score, upvote_ratio,
                comment_count, analysis_score, analysis_reasons_json, matched_keywords_json,
                created_utc, ingested_at, raw_payload_json
            FROM {LEGACY_THREADS_TABLE};
            """
        )

        if LEGACY_COMMENTS_TABLE in tables:
            connection.executescript(
                f"""
                CREATE TABLE {COMMENTS_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL REFERENCES {THREADS_TABLE}(id) ON DELETE CASCADE,
                    external_id TEXT NOT NULL,
                    author TEXT NOT NULL,
                    body TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    created_utc INTEGER NOT NULL,
                    permalink TEXT NOT NULL,
                    UNIQUE(thread_id, external_id)
                );

                INSERT INTO {COMMENTS_TABLE} (
                    id, thread_id, external_id, author, body, score, created_utc, permalink
                )
                SELECT
                    id, thread_id, external_id, author, body, score, created_utc, permalink
                FROM {LEGACY_COMMENTS_TABLE};
                """
            )

    def _cleanup_legacy_tables(self, connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if THREADS_TABLE not in tables:
            return
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            if LEGACY_COMMENTS_TABLE in tables:
                connection.execute(f"DROP TABLE IF EXISTS {LEGACY_COMMENTS_TABLE}")
            if LEGACY_THREADS_TABLE in tables:
                connection.execute(f"DROP TABLE IF EXISTS {LEGACY_THREADS_TABLE}")
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
