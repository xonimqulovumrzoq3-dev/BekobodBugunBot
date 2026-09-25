from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from telegram import Message
from telegram.ext import ContextTypes

from auth_store import AuthStore
from bot import (
    AUTH_BLOCKED,
    PASSWORD_FAILURE,
    PASSWORD_PROMPT,
    PASSWORD_SUCCESS,
    build_application,
    handle_login,
    handle_logout,
    handle_private_message,
    handle_start,
    handle_status,
    process_message,
)
from config import Settings


class FakeMessage:
    def __init__(
        self,
        *,
        caption: str | None,
        text: str | None = None,
        has_media: bool = True,
        user_id: int = 123456789,
        username: str = "owner",
    ) -> None:
        self.message_id = 42
        self.chat_id = 1001
        self.from_user = SimpleNamespace(id=user_id, username=username)
        self.text = text
        self.entities = ()
        self.caption = caption
        self.caption_entities = ()
        self.photo = (object(),) if has_media else None
        self.edit_caption = AsyncMock()
        self.edit_text = AsyncMock()
        self.copy = AsyncMock()
        self.reply_text = AsyncMock()


class BotMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            bot_token="123:token",
            channel_id="@channel",
            bot_password="correct horse battery staple",
            allowed_user_ids=frozenset({123456789}),
            removable_domains=frozenset(),
            removable_links=(),
            log_level="INFO",
        )
        self.bot = SimpleNamespace(id=999, send_message=AsyncMock())
        self.auth_store = AuthStore(":memory:")
        self.addCleanup(self.auth_store.close)
        self.auth_store.mark_authenticated(123456789)
        self.context = cast(
            ContextTypes.DEFAULT_TYPE,
            SimpleNamespace(
                application=SimpleNamespace(
                    bot_data={
                        "settings": self.settings,
                        "auth_store": self.auth_store,
                    }
                ),
                bot=self.bot,
            ),
        )

    def test_build_application_installs_authentication_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                bot_token="123:token",
                channel_id="@channel",
                bot_password="correct horse battery staple",
                allowed_user_ids=frozenset({123456789}),
                removable_domains=frozenset(),
                removable_links=(),
                log_level="INFO",
                auth_db_path=f"{directory}/auth.sqlite3",
            )

            application = build_application(settings)
            auth_store = application.bot_data["auth_store"]
            self.addCleanup(auth_store.close)

            self.assertIsInstance(auth_store, AuthStore)
            self.assertEqual(auth_store.database_path, f"{directory}/auth.sqlite3")

    def _process(
        self,
        message: FakeMessage,
        *,
        edit_original: bool,
    ) -> None:
        asyncio.run(
            process_message(
                cast(Message, message),
                self.context,
                self.settings,
                edit_original=edit_original,
            )
        )

    def _handle_private(
        self,
        message: FakeMessage,
        handler=handle_private_message,
    ) -> None:
        asyncio.run(
            handler(
                cast(object, SimpleNamespace(effective_message=message)),
                self.context,
            )
        )

    def test_channel_image_uses_edit_caption_even_when_text_is_present(self) -> None:
        message = FakeMessage(caption="Photo caption", text="client text")

        self._process(message, edit_original=True)

        message.edit_caption.assert_awaited_once()
        message.edit_text.assert_not_awaited()
        edit_call = message.edit_caption.await_args
        self.assertIsNotNone(edit_call)
        assert edit_call is not None
        caption = edit_call.args[0]
        self.assertTrue(caption.startswith("Photo caption"))
        self.assertIn("Telegram | Instagram", caption)

    def test_unauthorized_user_is_rejected_before_processing(self) -> None:
        message = FakeMessage(
            caption="Photo caption",
            user_id=987654321,
            username="intruder",
        )

        with self.assertLogs("bot", level="WARNING") as logs, patch(
            "bot.sanitize_message"
        ) as sanitize:
            self._process(message, edit_original=False)

        sanitize.assert_not_called()
        message.copy.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        message.edit_caption.assert_not_awaited()
        message.edit_text.assert_not_awaited()
        message.reply_text.assert_awaited_once_with(
            "⛔ You are not authorized to use this bot."
        )
        log_message = logs.output[0]
        self.assertIn("id=987654321", log_message)
        self.assertIn("username=@intruder", log_message)
        self.assertIn("timestamp=", log_message)

    def test_authorized_unauthenticated_post_is_not_published(self) -> None:
        self.auth_store.logout(123456789)
        message = FakeMessage(caption="Photo caption")

        with patch("bot.sanitize_message") as sanitize:
            self._handle_private(message)

        sanitize.assert_not_called()
        message.copy.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        message.reply_text.assert_awaited_once_with(PASSWORD_PROMPT)

    def test_correct_password_authenticates_user(self) -> None:
        self.auth_store.logout(123456789)
        trigger = FakeMessage(caption=None, text="hello", has_media=False)
        message = FakeMessage(
            caption=None,
            text=self.settings.bot_password,
            has_media=False,
        )

        self._handle_private(trigger)
        self.assertFalse(self.auth_store.is_authenticated(123456789))
        self._handle_private(message)

        trigger.reply_text.assert_awaited_once_with(PASSWORD_PROMPT)
        self.assertTrue(self.auth_store.is_authenticated(123456789))
        self.bot.send_message.assert_not_awaited()
        message.reply_text.assert_awaited_once_with(PASSWORD_SUCCESS)

    def test_incorrect_password_is_rejected_and_rate_limited(self) -> None:
        self.auth_store.logout(123456789)
        trigger = FakeMessage(caption=None, text="hello", has_media=False)
        messages = [
            FakeMessage(caption=None, text="wrong", has_media=False)
            for _ in range(5)
        ]

        self._handle_private(trigger)
        trigger.reply_text.assert_awaited_once_with(PASSWORD_PROMPT)

        with self.assertLogs("bot", level="WARNING") as logs:
            for message in messages:
                self._handle_private(message)

        self.assertNotIn(self.settings.bot_password, "\n".join(logs.output))
        self.assertFalse(self.auth_store.is_authenticated(123456789))
        self.assertTrue(self.auth_store.is_blocked(123456789))
        messages[-1].reply_text.assert_awaited_once_with(AUTH_BLOCKED)
        for index, message in enumerate(messages):
            expected_response = AUTH_BLOCKED if index == 4 else PASSWORD_FAILURE
            message.reply_text.assert_awaited_once_with(expected_response)
            self.bot.send_message.assert_not_awaited()

        blocked_message = FakeMessage(
            caption=None,
            text=self.settings.bot_password,
            has_media=False,
        )
        self._handle_private(blocked_message)
        blocked_message.reply_text.assert_awaited_once_with(AUTH_BLOCKED)
        self.assertFalse(self.auth_store.is_authenticated(123456789))

    def test_logout_requires_password_again(self) -> None:
        self.auth_store.mark_authenticated(123456789)
        message = FakeMessage(caption=None, text="/logout", has_media=False)

        self._handle_private(message, handle_logout)

        self.assertFalse(self.auth_store.is_authenticated(123456789))
        message.reply_text.assert_awaited_once_with(
            "✅ You have been logged out. Use /login to authenticate again."
        )

    def test_status_is_available_after_authentication(self) -> None:
        self.auth_store.mark_authenticated(123456789)
        self.auth_store.record_successful_post(123456789)
        message = FakeMessage(caption=None, text="/status", has_media=False)

        self._handle_private(message, handle_status)

        status_call = message.reply_text.await_args
        self.assertIsNotNone(status_call)
        assert status_call is not None
        response = status_call.args[0]
        self.assertIn("Authentication status: authenticated", response)
        self.assertIn("Telegram user ID: 123456789", response)
        self.assertIn("Successful posts: 1", response)
        self.assertNotIn(self.settings.bot_password, response)

    def test_start_and_login_prompt_for_password(self) -> None:
        self.auth_store.logout(123456789)
        start_message = FakeMessage(caption=None, text="/start", has_media=False)
        login_message = FakeMessage(caption=None, text="/login", has_media=False)

        self._handle_private(start_message, handle_start)
        self._handle_private(login_message, handle_login)

        start_message.reply_text.assert_awaited_once_with(PASSWORD_PROMPT)
        login_message.reply_text.assert_awaited_once_with(PASSWORD_PROMPT)

    def test_private_image_is_copied_to_configured_channel(self) -> None:
        message = FakeMessage(caption="Photo caption")

        self._process(message, edit_original=False)

        message.copy.assert_awaited_once()
        self.assertIsNotNone(message.copy.await_args)
        copy_call = message.copy.await_args
        assert copy_call is not None
        self.assertEqual(copy_call.kwargs["chat_id"], "@channel")
        self.assertTrue(copy_call.kwargs["caption"].startswith("Photo caption"))
        self.assertIn("Telegram | Instagram", copy_call.kwargs["caption"])
        self.bot.send_message.assert_not_awaited()
        message.reply_text.assert_awaited_once_with(
            "Published to the configured channel."
        )

    def test_private_image_without_caption_is_published(self) -> None:
        message = FakeMessage(caption=None)

        self._process(message, edit_original=False)

        message.copy.assert_awaited_once()
        copy_call = message.copy.await_args
        assert copy_call is not None
        self.assertEqual(copy_call.kwargs["chat_id"], "@channel")
        self.assertIn("Telegram | Instagram", copy_call.kwargs["caption"])

    def test_private_text_is_sent_to_configured_channel(self) -> None:
        message = FakeMessage(caption=None, text="Post text", has_media=False)

        self._process(message, edit_original=False)

        message.copy.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        send_call = self.bot.send_message.await_args
        self.assertIsNotNone(send_call)
        assert send_call is not None
        self.assertEqual(send_call.kwargs["chat_id"], "@channel")
        self.assertTrue(send_call.kwargs["text"].startswith("Post text"))
        self.assertIn("Telegram | Instagram", send_call.kwargs["text"])
        message.reply_text.assert_awaited_once_with(
            "Published to the configured channel."
        )

    def test_channel_post_processing_is_not_blocked_by_allowlist(self) -> None:
        message = FakeMessage(caption="Photo caption", user_id=987654321)

        self._process(message, edit_original=True)

        message.edit_caption.assert_awaited_once()
        message.copy.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    def test_channel_image_without_caption_still_keeps_media(self) -> None:
        message = FakeMessage(caption=None)

        self._process(message, edit_original=True)

        message.edit_caption.assert_awaited_once()
        message.edit_text.assert_not_awaited()
        edit_call = message.edit_caption.await_args
        self.assertIsNotNone(edit_call)
        assert edit_call is not None
        self.assertIn("Telegram | Instagram", edit_call.args[0])


if __name__ == "__main__":
    unittest.main()
