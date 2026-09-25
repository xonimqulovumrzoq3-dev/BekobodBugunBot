from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

MAX_FAILED_ATTEMPTS: Final = 5
FAILURE_WINDOW_SECONDS: Final = 15 * 60
BLOCK_DURATION_SECONDS: Final = 15 * 60


@dataclass(frozen=True, slots=True)
class AuthStatus:
    authenticated: bool
    authenticated_at: str | None
    successful_posts: int
    failed_attempts: int
    blocked: bool


@dataclass(frozen=True, slots=True)
class FailedAttempt:
    blocked: bool
    remaining_attempts: int


class AuthStore:
    def __init__(self, database_path: str) -> None:
        if database_path != ":memory:":
            path = Path(database_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            database_path = str(path)
        self.database_path = database_path
        self._connection = sqlite3.connect(
            database_path,
            timeout=10,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    authenticated INTEGER NOT NULL DEFAULT 0,
                    authenticated_at TEXT,
                    successful_posts INTEGER NOT NULL DEFAULT 0,
                    failed_attempts_total INTEGER NOT NULL DEFAULT 0,
                    failed_attempts_window INTEGER NOT NULL DEFAULT 0,
                    failure_window_started_at REAL,
                    blocked_until REAL
                )
                """
            )

    def _ensure_user(self, user_id: int) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)",
                (user_id,),
            )

    def _get_row(self, user_id: int) -> sqlite3.Row:
        self._ensure_user(user_id)
        row = self._connection.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Could not initialize authentication user")
        return row

    def is_authenticated(self, user_id: int) -> bool:
        return bool(self._get_row(user_id)["authenticated"])

    def is_blocked(self, user_id: int, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        blocked_until = self._get_row(user_id)["blocked_until"]
        return blocked_until is not None and blocked_until > current_time

    def mark_authenticated(self, user_id: int) -> AuthStatus:
        self._ensure_user(user_id)
        authenticated_at = datetime.now(timezone.utc).isoformat()
        with self._connection:
            self._connection.execute(
                """
                UPDATE users
                SET authenticated = 1,
                    authenticated_at = ?,
                    failed_attempts_window = 0,
                    failure_window_started_at = NULL,
                    blocked_until = NULL
                WHERE telegram_user_id = ?
                """,
                (authenticated_at, user_id),
            )
        return self.get_status(user_id)

    def record_failed_attempt(
        self,
        user_id: int,
        now: float | None = None,
    ) -> FailedAttempt:
        current_time = time.time() if now is None else now
        row = self._get_row(user_id)
        blocked_until = row["blocked_until"]
        if blocked_until is not None and blocked_until > current_time:
            return FailedAttempt(blocked=True, remaining_attempts=0)

        window_started_at = row["failure_window_started_at"]
        window_attempts = row["failed_attempts_window"]
        if (
            window_started_at is None
            or current_time - window_started_at >= FAILURE_WINDOW_SECONDS
        ):
            window_started_at = current_time
            window_attempts = 0

        window_attempts += 1
        blocked = window_attempts >= MAX_FAILED_ATTEMPTS
        next_blocked_until = current_time + BLOCK_DURATION_SECONDS if blocked else None
        with self._connection:
            self._connection.execute(
                """
                UPDATE users
                SET failed_attempts_total = failed_attempts_total + 1,
                    failed_attempts_window = ?,
                    failure_window_started_at = ?,
                    blocked_until = ?
                WHERE telegram_user_id = ?
                """,
                (
                    window_attempts,
                    window_started_at,
                    next_blocked_until,
                    user_id,
                ),
            )
        return FailedAttempt(
            blocked=blocked,
            remaining_attempts=max(MAX_FAILED_ATTEMPTS - window_attempts, 0),
        )

    def logout(self, user_id: int) -> None:
        self._ensure_user(user_id)
        with self._connection:
            self._connection.execute(
                """
                UPDATE users
                SET authenticated = 0,
                    authenticated_at = NULL,
                    failed_attempts_window = 0,
                    failure_window_started_at = NULL,
                    blocked_until = NULL
                WHERE telegram_user_id = ?
                """,
                (user_id,),
            )

    def get_status(self, user_id: int) -> AuthStatus:
        row = self._get_row(user_id)
        blocked_until = row["blocked_until"]
        return AuthStatus(
            authenticated=bool(row["authenticated"]),
            authenticated_at=row["authenticated_at"],
            successful_posts=row["successful_posts"],
            failed_attempts=row["failed_attempts_total"],
            blocked=blocked_until is not None and blocked_until > time.time(),
        )

    def record_successful_post(self, user_id: int) -> bool:
        self._ensure_user(user_id)
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE users
                SET successful_posts = successful_posts + 1
                WHERE telegram_user_id = ? AND authenticated = 1
                """,
                (user_id,),
            )
        return cursor.rowcount == 1
