from __future__ import annotations

import hmac
import logging
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone

from telegram import Message, MessageEntity, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from auth_store import AuthStatus, AuthStore
from config import Settings, load_settings
from link_utils import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    FooterConfig,
    LinkPolicy,
    SanitizedMessage,
    sanitize_message,
)

LOGGER = logging.getLogger(__name__)
PASSWORD_PROMPT = "🔐 Please enter the password to use this bot."
PASSWORD_SUCCESS = "✅ Authentication successful. You can now send posts."
PASSWORD_FAILURE = "❌ Incorrect password."
AUTH_BLOCKED = "⏳ Too many incorrect password attempts. Try again later."
AUTH_UNAVAILABLE = "⚠️ Authentication service is unavailable. Please try again later."
_PASSWORD_PROMPTS_KEY = "password_prompts"
_CAPTION_MEDIA_FIELDS = (
    "animation",
    "audio",
    "document",
    "paid_media",
    "photo",
    "video",
    "video_note",
    "voice",
)


def _has_caption_media(message: Message) -> bool:
    return any(
        getattr(message, field, None) is not None
        for field in _CAPTION_MEDIA_FIELDS
    )


def _message_content(
    message: Message,
) -> tuple[str | None, tuple[MessageEntity, ...], bool]:
    # Media must use the caption edit path even if a client exposes text as well.
    if _has_caption_media(message) or message.caption is not None:
        return message.caption or "", tuple(message.caption_entities or ()), True
    if message.text is not None:
        return message.text, tuple(message.entities or ()), False
    return None, (), False


def _same_entities(
    left: Sequence[MessageEntity],
    right: Sequence[MessageEntity],
) -> bool:
    return [entity.to_dict() for entity in left] == [
        entity.to_dict() for entity in right
    ]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.application.bot_data.get("settings")
    if not isinstance(settings, Settings):
        raise TypeError("Bot settings are not initialized")
    return settings


def _auth_store(context: ContextTypes.DEFAULT_TYPE) -> AuthStore:
    auth_store = context.application.bot_data.get("auth_store")
    if not isinstance(auth_store, AuthStore):
        raise TypeError("Authentication store is not initialized")
    return auth_store


def _sender_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user is not None else None


def _password_prompted(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    prompts = context.application.bot_data.get(_PASSWORD_PROMPTS_KEY)
    return isinstance(prompts, set) and user_id in prompts


def _set_password_prompted(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> None:
    prompts = context.application.bot_data.get(_PASSWORD_PROMPTS_KEY)
    if not isinstance(prompts, set):
        prompts = set()
        context.application.bot_data[_PASSWORD_PROMPTS_KEY] = prompts
    prompts.add(user_id)


def _clear_password_prompted(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> None:
    prompts = context.application.bot_data.get(_PASSWORD_PROMPTS_KEY)
    if isinstance(prompts, set):
        prompts.discard(user_id)


def _is_authorized(message: Message, settings: Settings) -> bool:
    sender_id = _sender_id(message)
    return sender_id is not None and settings.is_user_allowed(sender_id)


async def _reply(message: Message, text: str) -> None:
    try:
        await message.reply_text(text)
    except TelegramError:
        LOGGER.exception("Could not send Telegram bot response")


async def _reject_unauthorized(message: Message) -> None:
    sender = message.from_user
    user_id = sender.id if sender is not None else None
    username = (
        f"@{sender.username}"
        if sender is not None and sender.username
        else "unavailable"
    )
    LOGGER.warning(
        "Unauthorized Telegram user id=%s username=%s timestamp=%s",
        user_id if user_id is not None else "unavailable",
        username,
        datetime.now(timezone.utc).isoformat(),
    )
    await _reply(message, "⛔ You are not authorized to use this bot.")


def _log_failed_authentication(message: Message, *, blocked: bool) -> None:
    sender = message.from_user
    user_id = sender.id if sender is not None else None
    username = (
        f"@{sender.username}"
        if sender is not None and sender.username
        else "unavailable"
    )
    LOGGER.warning(
        "Failed Telegram authentication user id=%s username=%s blocked=%s timestamp=%s",
        user_id if user_id is not None else "unavailable",
        username,
        blocked,
        datetime.now(timezone.utc).isoformat(),
    )


async def _authentication_status(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> AuthStatus | None:
    try:
        return _auth_store(context).get_status(user_id)
    except sqlite3.Error:
        LOGGER.exception("Could not read authentication state for Telegram user %s", user_id)
        await _reply(message, AUTH_UNAVAILABLE)
        return None


async def _require_authentication(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> bool:
    user_id = _sender_id(message)
    if user_id is None or not settings.is_user_allowed(user_id):
        await _reject_unauthorized(message)
        return False

    status = await _authentication_status(message, context, user_id)
    if status is None:
        return False
    if status.authenticated:
        return True
    if status.blocked:
        await _reply(message, AUTH_BLOCKED)
        return False
    _set_password_prompted(context, user_id)
    await _reply(message, PASSWORD_PROMPT)
    return False


async def _handle_password_message(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    user_id: int,
) -> None:
    if not _password_prompted(context, user_id):
        _set_password_prompted(context, user_id)
        await _reply(message, PASSWORD_PROMPT)
        return

    try:
        auth_store = _auth_store(context)
        if auth_store.is_blocked(user_id):
            _log_failed_authentication(message, blocked=True)
            await _reply(message, AUTH_BLOCKED)
            return

        if message.text is None:
            await _reply(message, PASSWORD_PROMPT)
            return

        if hmac.compare_digest(
            message.text.encode("utf-8"),
            settings.bot_password.encode("utf-8"),
        ):
            auth_store.mark_authenticated(user_id)
            _clear_password_prompted(context, user_id)
            await _reply(message, PASSWORD_SUCCESS)
            return

        failed_attempt = auth_store.record_failed_attempt(user_id)
        _log_failed_authentication(message, blocked=failed_attempt.blocked)
        await _reply(
            message,
            AUTH_BLOCKED if failed_attempt.blocked else PASSWORD_FAILURE,
        )
    except sqlite3.Error:
        LOGGER.exception("Could not update authentication state for Telegram user %s", user_id)
        await _reply(message, AUTH_UNAVAILABLE)


async def _publish_to_channel(
    message: Message,
    sanitized: SanitizedMessage,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    *,
    is_caption: bool,
) -> None:
    if is_caption:
        await message.copy(
            chat_id=settings.channel_id,
            caption=sanitized.text,
            caption_entities=sanitized.entities,
        )
    else:
        await context.bot.send_message(
            chat_id=settings.channel_id,
            text=sanitized.text,
            entities=sanitized.entities,
            disable_web_page_preview=True,
        )


async def process_message(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    *,
    edit_original: bool,
) -> None:
    if message.from_user is not None and message.from_user.id == context.bot.id:
        return

    if not edit_original and not await _require_authentication(message, context, settings):
        return

    content, entities, is_caption = _message_content(message)
    if content is None:
        return

    policy = LinkPolicy(
        removable_domains=settings.removable_domains,
        removable_links=settings.removable_links,
    )
    max_length = TELEGRAM_CAPTION_LIMIT if is_caption else TELEGRAM_TEXT_LIMIT
    footer = FooterConfig(
        telegram_url=settings.telegram_footer_url,
        instagram_url=settings.instagram_footer_url,
    )
    sanitized: SanitizedMessage = sanitize_message(
        content,
        entities,
        policy,
        max_length,
        footer=footer,
    )
    if (
        edit_original
        and sanitized.text == content
        and _same_entities(entities, sanitized.entities)
    ):
        return

    try:
        # Publishing adjusted entities keeps Telegram formatting without reparsing user HTML.
        if not edit_original:
            await _publish_to_channel(
                message,
                sanitized,
                context,
                settings,
                is_caption=is_caption,
            )
            user_id = _sender_id(message)
            if user_id is not None:
                try:
                    if not _auth_store(context).record_successful_post(user_id):
                        LOGGER.warning(
                            "Could not record successful post for Telegram user %s",
                            user_id,
                        )
                except sqlite3.Error:
                    LOGGER.exception(
                        "Could not update post statistics for Telegram user %s",
                        user_id,
                    )
            await _reply(message, "Published to the configured channel.")
        elif is_caption:
            await message.edit_caption(
                sanitized.text,
                caption_entities=sanitized.entities,
            )
        else:
            await message.edit_text(
                sanitized.text,
                entities=sanitized.entities,
                disable_web_page_preview=True,
            )
    except TelegramError as error:
        if "message is not modified" in str(error).lower():
            LOGGER.debug("Message %s was already processed", message.message_id)
        else:
            LOGGER.exception(
                "Could not process message %s in chat %s",
                message.message_id,
                message.chat_id,
            )


async def handle_channel_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    # Channel posts and edited channel posts are separate Bot API update types.
    message = update.channel_post or update.edited_channel_post
    settings = _settings(context)
    if message is not None and settings.matches_chat(message.chat):
        await process_message(
            message,
            context,
            settings,
            edit_original=True,
        )


async def handle_private_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    if message is None:
        return
    if message.from_user is not None and message.from_user.id == context.bot.id:
        return

    settings = _settings(context)
    user_id = _sender_id(message)
    if user_id is None or not settings.is_user_allowed(user_id):
        await _reject_unauthorized(message)
        return

    status = await _authentication_status(message, context, user_id)
    if status is None:
        return
    if status.blocked:
        await _reply(message, AUTH_BLOCKED)
        return
    if not status.authenticated:
        if not _password_prompted(context, user_id):
            _set_password_prompted(context, user_id)
            await _reply(message, PASSWORD_PROMPT)
            return
        await _handle_password_message(message, context, settings, user_id)
        return

    _clear_password_prompted(context, user_id)
    await process_message(
        message,
        context,
        settings,
        edit_original=False,
    )


async def _authorized_command_user(
    message: Message | None,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[Message, Settings, int] | None:
    if message is None:
        return None
    if message.from_user is not None and message.from_user.id == context.bot.id:
        return None
    settings = _settings(context)
    user_id = _sender_id(message)
    if user_id is None or not settings.is_user_allowed(user_id):
        await _reject_unauthorized(message)
        return None
    return message, settings, user_id


async def _handle_login(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    authorized_user = await _authorized_command_user(message, context)
    if authorized_user is None:
        return
    message, _, user_id = authorized_user
    status = await _authentication_status(message, context, user_id)
    if status is None:
        return
    if status.blocked:
        await _reply(message, AUTH_BLOCKED)
    elif status.authenticated:
        _clear_password_prompted(context, user_id)
        await _reply(message, "You are already authenticated. You can send posts.")
    else:
        _set_password_prompted(context, user_id)
        await _reply(message, PASSWORD_PROMPT)


async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _handle_login(update, context)


async def handle_login(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _handle_login(update, context)


async def handle_logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    authorized_user = await _authorized_command_user(message, context)
    if authorized_user is None:
        return
    message, _, user_id = authorized_user
    _clear_password_prompted(context, user_id)
    status = await _authentication_status(message, context, user_id)
    if status is None:
        return
    if not status.authenticated:
        await _reply(message, "You are not authenticated. Use /login to authenticate.")
        return
    try:
        _auth_store(context).logout(user_id)
    except sqlite3.Error:
        LOGGER.exception("Could not log out Telegram user %s", user_id)
        await _reply(message, AUTH_UNAVAILABLE)
        return
    await _reply(message, "✅ You have been logged out. Use /login to authenticate again.")


async def handle_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    authorized_user = await _authorized_command_user(message, context)
    if authorized_user is None:
        return
    message, _, user_id = authorized_user
    status = await _authentication_status(message, context, user_id)
    if status is None:
        return
    if status.blocked:
        await _reply(message, AUTH_BLOCKED)
        return
    if not status.authenticated:
        _set_password_prompted(context, user_id)
        await _reply(message, PASSWORD_PROMPT)
        return
    await _reply(
        message,
        "🔐 Authentication status: authenticated\n"
        f"Telegram user ID: {user_id}\n"
        f"Successful posts: {status.successful_posts}\n"
        f"Failed authentication attempts: {status.failed_attempts}",
    )


def build_application(settings: Settings) -> Application:
    application = Application.builder().token(settings.bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["auth_store"] = AuthStore(settings.auth_db_path)
    application.add_handler(
        CommandHandler("start", handle_start, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("login", handle_login, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("logout", handle_logout, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("status", handle_status, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post)
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST, handle_channel_post)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE,
            handle_private_message,
        )
    )
    return application


def main() -> None:
    try:
        settings = load_settings()
    except ValueError as error:
        raise SystemExit(str(error)) from error

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=settings.log_level,
    )
    try:
        application = build_application(settings)
    except (OSError, sqlite3.Error) as error:
        raise SystemExit(f"Could not initialize authentication storage: {error}") from error
    application.run_polling(
        allowed_updates=[
            "message",
            "channel_post",
            "edited_channel_post",
        ]
    )


if __name__ == "__main__":
    main()
