from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


SCHEMA = """
CREATE TABLE IF NOT EXISTS background_usage (
    path TEXT PRIMARY KEY,
    duration_seconds REAL NOT NULL,
    used_until_seconds REAL NOT NULL DEFAULT 0,
    exhausted INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class BackgroundUsageSelection:
    path: Path
    start_seconds: float
    duration_seconds: float
    source_duration_seconds: float
    used_until_seconds: float
    exhausted: bool


class BackgroundUsageDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def select_segment(
        self,
        paths: tuple[Path, ...],
        needed_seconds: float,
        duration_probe: Callable[[Path], float],
    ) -> BackgroundUsageSelection:
        if needed_seconds <= 0:
            raise SystemExit("Video duration must be positive.")
        candidates = tuple(path.resolve() for path in paths)
        if not candidates:
            raise SystemExit("No background videos found.")

        self.initialize()
        with self.connect() as connection:
            selection = self._select_from_candidates(connection, candidates, needed_seconds, duration_probe)
            if selection is not None:
                return selection

        raise SystemExit(
            "All matching background videos are fully used or too short for "
            f"{needed_seconds:.2f} seconds. Add more background videos or reset "
            f"{self.db_path} manually."
        )

    def _select_from_candidates(
        self,
        connection: sqlite3.Connection,
        candidates: tuple[Path, ...],
        needed_seconds: float,
        duration_probe: Callable[[Path], float],
    ) -> BackgroundUsageSelection | None:
        shuffled = list(candidates)
        random.shuffle(shuffled)
        for path in shuffled:
            row = self._get_or_create_row(connection, path, duration_probe)
            duration = float(row["duration_seconds"])
            used_until = min(max(0.0, float(row["used_until_seconds"])), duration)
            exhausted = bool(row["exhausted"])
            if exhausted:
                continue

            remaining = duration - used_until
            if remaining + 0.001 < needed_seconds:
                self._mark_exhausted(connection, path, duration)
                continue

            start = used_until
            next_used_until = min(duration, used_until + needed_seconds)
            next_exhausted = next_used_until >= duration - 0.001
            self._save_usage(connection, path, duration, next_used_until, next_exhausted)
            return BackgroundUsageSelection(
                path=path,
                start_seconds=start,
                duration_seconds=needed_seconds,
                source_duration_seconds=duration,
                used_until_seconds=next_used_until,
                exhausted=next_exhausted,
            )
        return None

    def _get_or_create_row(
        self,
        connection: sqlite3.Connection,
        path: Path,
        duration_probe: Callable[[Path], float],
    ) -> sqlite3.Row:
        key = str(path.resolve())
        row = connection.execute(
            "SELECT * FROM background_usage WHERE path = ?",
            (key,),
        ).fetchone()
        duration = duration_probe(path)
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            connection.execute(
                """
                INSERT INTO background_usage (
                    path, duration_seconds, used_until_seconds, exhausted, updated_at
                ) VALUES (?, ?, 0, 0, ?)
                """,
                (key, duration, now),
            )
            row = connection.execute(
                "SELECT * FROM background_usage WHERE path = ?",
                (key,),
            ).fetchone()
        elif abs(float(row["duration_seconds"]) - duration) > 0.5:
            connection.execute(
                """
                UPDATE background_usage
                SET duration_seconds = ?, used_until_seconds = 0, exhausted = 0, updated_at = ?
                WHERE path = ?
                """,
                (duration, now, key),
            )
            row = connection.execute(
                "SELECT * FROM background_usage WHERE path = ?",
                (key,),
            ).fetchone()
        return row

    def _mark_exhausted(self, connection: sqlite3.Connection, path: Path, duration: float) -> None:
        self._save_usage(connection, path, duration, duration, True)

    def _save_usage(
        self,
        connection: sqlite3.Connection,
        path: Path,
        duration: float,
        used_until: float,
        exhausted: bool,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            UPDATE background_usage
            SET duration_seconds = ?,
                used_until_seconds = ?,
                exhausted = ?,
                last_used_at = ?,
                updated_at = ?
            WHERE path = ?
            """,
            (duration, used_until, int(exhausted), now, now, str(path.resolve())),
        )
