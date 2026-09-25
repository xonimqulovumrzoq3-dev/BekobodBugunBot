from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth_store import (
    BLOCK_DURATION_SECONDS,
    MAX_FAILED_ATTEMPTS,
    AuthStore,
)


class AuthStoreTests(unittest.TestCase):
    def test_authentication_and_post_count_survive_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "auth.sqlite3")
            first_store = AuthStore(database_path)
            first_store.mark_authenticated(123)
            first_store.record_successful_post(123)
            first_store.close()

            second_store = AuthStore(database_path)
            try:
                self.assertTrue(second_store.is_authenticated(123))
                self.assertEqual(second_store.get_status(123).successful_posts, 1)
            finally:
                second_store.close()

    def test_failed_attempts_are_limited_and_block(self) -> None:
        store = AuthStore(":memory:")
        self.addCleanup(store.close)

        for attempt in range(1, MAX_FAILED_ATTEMPTS):
            result = store.record_failed_attempt(123, now=100)
            self.assertFalse(result.blocked)
            self.assertEqual(result.remaining_attempts, MAX_FAILED_ATTEMPTS - attempt)

        result = store.record_failed_attempt(123, now=100)
        self.assertTrue(result.blocked)
        self.assertEqual(result.remaining_attempts, 0)
        self.assertTrue(store.is_blocked(123, now=100))
        self.assertFalse(
            store.is_blocked(123, now=100 + BLOCK_DURATION_SECONDS + 1)
        )
        self.assertEqual(store.get_status(123).failed_attempts, MAX_FAILED_ATTEMPTS)

    def test_failed_attempt_block_survives_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "auth.sqlite3")
            first_store = AuthStore(database_path)
            for _ in range(MAX_FAILED_ATTEMPTS):
                first_store.record_failed_attempt(123, now=100)
            first_store.close()

            second_store = AuthStore(database_path)
            try:
                self.assertTrue(second_store.is_blocked(123, now=100))
            finally:
                second_store.close()

    def test_authentication_resets_current_failure_window(self) -> None:
        store = AuthStore(":memory:")
        self.addCleanup(store.close)

        store.record_failed_attempt(123, now=100)
        store.mark_authenticated(123)

        status = store.get_status(123)
        self.assertTrue(status.authenticated)
        self.assertEqual(status.failed_attempts, 1)
        self.assertFalse(store.is_blocked(123, now=100))

    def test_logout_clears_authentication_but_keeps_history(self) -> None:
        store = AuthStore(":memory:")
        self.addCleanup(store.close)

        store.mark_authenticated(123)
        store.record_successful_post(123)
        store.record_failed_attempt(123, now=100)
        store.logout(123)

        status = store.get_status(123)
        self.assertFalse(status.authenticated)
        self.assertEqual(status.successful_posts, 1)
        self.assertEqual(status.failed_attempts, 1)

    def test_schema_does_not_store_passwords(self) -> None:
        store = AuthStore(":memory:")
        self.addCleanup(store.close)

        columns = {
            row[1]
            for row in store._connection.execute("PRAGMA table_info(users)").fetchall()
        }

        self.assertNotIn("password", columns)
        self.assertNotIn("bot_password", columns)

    def test_mutating_operations_create_user_rows(self) -> None:
        store = AuthStore(":memory:")
        self.addCleanup(store.close)

        store.logout(123)
        store.mark_authenticated(123)
        store.logout(123)

        self.assertFalse(store.is_authenticated(123))


if __name__ == "__main__":
    unittest.main()
