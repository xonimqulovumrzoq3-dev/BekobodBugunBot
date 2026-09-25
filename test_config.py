from __future__ import annotations

import unittest

from telegram import Chat

from config import Settings


class SettingsTests(unittest.TestCase):
    def test_loads_required_and_selective_configuration(self) -> None:
        settings = Settings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "TARGET_CHANNEL_ID": "@My_Channel",
                "ALLOWED_USER_IDS": "123456789, 987654321",
                "REMOVABLE_DOMAINS": "t.me, telegram.me ,instagram.com",
                "REMOVABLE_LINKS": "https://old.example/profile",
                "TELEGRAM_FOOTER_URL": "https://t.me/custom-telegram",
                "INSTAGRAM_FOOTER_URL": "https://www.instagram.com/custom-instagram",
                "LOG_LEVEL": "debug",
            }
        )

        self.assertEqual(settings.channel_id, "@my_channel")
        self.assertEqual(settings.allowed_user_ids, {123456789, 987654321})
        self.assertEqual(settings.removable_domains, {"t.me", "telegram.me", "instagram.com"})
        self.assertEqual(settings.removable_links, ("https://old.example/profile",))
        self.assertEqual(settings.telegram_footer_url, "https://t.me/custom-telegram")
        self.assertEqual(
            settings.instagram_footer_url,
            "https://www.instagram.com/custom-instagram",
        )
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.bot_password, "secret")
        self.assertEqual(settings.auth_db_path, "auth.sqlite3")

    def test_uses_default_footer_targets(self) -> None:
        settings = Settings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "CHANNEL_ID": "@channel",
                "ALLOWED_USER_IDS": "123456789",
            }
        )

        self.assertEqual(settings.telegram_footer_url, "https://t.me/bekobodbugun")
        self.assertEqual(
            settings.instagram_footer_url,
            "https://www.instagram.com/bekobodbugun",
        )

    def test_matches_numeric_and_username_channels(self) -> None:
        numeric = Settings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "TARGET_CHANNEL_ID": "-1001234567890",
                "ALLOWED_USER_IDS": "123456789",
            }
        )
        username = Settings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "TARGET_CHANNEL_ID": "@My_Channel",
                "ALLOWED_USER_IDS": "123456789",
            }
        )

        self.assertTrue(
            numeric.matches_chat(Chat(id=-1001234567890, type="channel"))
        )
        self.assertFalse(
            numeric.matches_chat(Chat(id=-1001234567891, type="channel"))
        )
        self.assertTrue(
            username.matches_chat(
                Chat(id=-1001234567890, type="channel", username="my_channel")
            )
        )
        self.assertFalse(
            username.matches_chat(
                Chat(id=-1001234567890, type="channel", username="other_channel")
            )
        )

    def test_rejects_missing_required_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "BOT_TOKEN"):
            Settings.from_env({"CHANNEL_ID": "@channel"})

        with self.assertRaisesRegex(ValueError, "CHANNEL_ID"):
            Settings.from_env({"BOT_TOKEN": "123:token", "BOT_PASSWORD": "secret"})

    def test_rejects_missing_or_blank_bot_password(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "BOT_PASSWORD"
            ):
                values = {
                    "BOT_TOKEN": "123:token",
                    "TARGET_CHANNEL_ID": "@channel",
                    "ALLOWED_USER_IDS": "123456789",
                }
                if value is not None:
                    values["BOT_PASSWORD"] = value
                Settings.from_env(values)

    def test_loads_auth_database_path(self) -> None:
        settings = Settings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "TARGET_CHANNEL_ID": "@channel",
                "ALLOWED_USER_IDS": "123456789",
                "AUTH_DB_PATH": "/var/lib/assistentbot/auth.sqlite3",
            }
        )

        self.assertEqual(settings.auth_db_path, "/var/lib/assistentbot/auth.sqlite3")

    def test_rejects_missing_or_invalid_allowed_user_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "ALLOWED_USER_IDS"):
            Settings.from_env(
                {
                    "BOT_TOKEN": "123:token",
                    "BOT_PASSWORD": "secret",
                    "TARGET_CHANNEL_ID": "@channel",
                }
            )

        for value in ("not-an-id", "0", "-123", "123,not-an-id", "123,"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "ALLOWED_USER_IDS"
            ):
                Settings.from_env(
                    {
                        "BOT_TOKEN": "123:token",
                        "BOT_PASSWORD": "secret",
                        "TARGET_CHANNEL_ID": "@channel",
                        "ALLOWED_USER_IDS": value,
                    }
                )

    def test_accepts_legacy_channel_id_and_rejects_conflicts(self) -> None:
        legacy = Settings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "CHANNEL_ID": "@My_Channel",
                "ALLOWED_USER_IDS": "123456789",
            }
        )
        self.assertEqual(legacy.channel_id, "@my_channel")

        with self.assertRaisesRegex(ValueError, "must match"):
            Settings.from_env(
                {
                    "BOT_TOKEN": "123:token",
                    "BOT_PASSWORD": "secret",
                    "TARGET_CHANNEL_ID": "@one",
                    "CHANNEL_ID": "@two",
                    "ALLOWED_USER_IDS": "123456789",
                }
            )

    def test_rejects_invalid_footer_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "Footer URLs"):
            Settings.from_env(
                {
                    "BOT_TOKEN": "123:token",
                    "BOT_PASSWORD": "secret",
                    "CHANNEL_ID": "@channel",
                    "ALLOWED_USER_IDS": "123456789",
                    "TELEGRAM_FOOTER_URL": "https://",
                }
            )

    def test_accepts_legacy_token_variable(self) -> None:
        settings = Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "123:token",
                "BOT_PASSWORD": "secret",
                "CHANNEL_ID": "@channel",
                "ALLOWED_USER_IDS": "123456789",
            }
        )

        self.assertEqual(settings.bot_token, "123:token")


if __name__ == "__main__":
    unittest.main()
