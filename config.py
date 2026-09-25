from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv
from telegram import Chat

from link_utils import INSTAGRAM_LINK, TELEGRAM_LINK, FooterConfig

_CHANNEL_ID_PATTERN = re.compile(r"-?\d+")
_ALLOWED_USER_ID_PATTERN = re.compile(r"[0-9]+")


def _split_values(environ: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in environ.get(name, "").split(",")
        if value.strip()
    )


def _parse_allowed_user_ids(value: str) -> frozenset[int]:
    raw_values = value.split(",")
    if not value.strip() or any(not raw_value.strip() for raw_value in raw_values):
        raise ValueError("ALLOWED_USER_IDS must contain at least one numeric Telegram user ID")

    user_ids: set[int] = set()
    for raw_value in raw_values:
        raw_id = raw_value.strip()
        if not _ALLOWED_USER_ID_PATTERN.fullmatch(raw_id):
            raise ValueError("ALLOWED_USER_IDS must contain only numeric Telegram user IDs")
        user_id = int(raw_id)
        if user_id <= 0:
            raise ValueError("ALLOWED_USER_IDS must contain only positive Telegram user IDs")
        user_ids.add(user_id)
    return frozenset(user_ids)


def _normalize_channel_id(value: str) -> str:
    channel_id = value.strip()
    if channel_id.startswith("@"):
        username = channel_id[1:].strip()
        if not username or any(character.isspace() for character in username):
            raise ValueError(
                "TARGET_CHANNEL_ID (or legacy CHANNEL_ID) must be a valid @username or numeric chat ID"
            )
        return f"@{username.lower()}"

    if _CHANNEL_ID_PATTERN.fullmatch(channel_id):
        return str(int(channel_id))

    raise ValueError(
        "TARGET_CHANNEL_ID (or legacy CHANNEL_ID) must be a valid @username or numeric chat ID"
    )


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    channel_id: str
    bot_password: str
    allowed_user_ids: frozenset[int]
    removable_domains: frozenset[str]
    removable_links: tuple[str, ...]
    log_level: str
    telegram_footer_url: str = TELEGRAM_LINK
    instagram_footer_url: str = INSTAGRAM_LINK
    auth_db_path: str = "auth.sqlite3"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> Settings:
        values = os.environ if environ is None else environ
        bot_token = values.get("BOT_TOKEN") or values.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            raise ValueError("BOT_TOKEN is required")

        bot_password = values.get("BOT_PASSWORD", "")
        if not bot_password or not bot_password.strip():
            raise ValueError("BOT_PASSWORD is required")

        target_channel_value = values.get("TARGET_CHANNEL_ID", "").strip()
        legacy_channel_value = values.get("CHANNEL_ID", "").strip()
        if (
            target_channel_value
            and legacy_channel_value
            and _normalize_channel_id(target_channel_value)
            != _normalize_channel_id(legacy_channel_value)
        ):
            raise ValueError(
                "TARGET_CHANNEL_ID and legacy CHANNEL_ID must match when both are set"
            )
        channel_value = target_channel_value or legacy_channel_value
        if not channel_value:
            raise ValueError("TARGET_CHANNEL_ID (or legacy CHANNEL_ID) is required")

        allowed_user_ids = _parse_allowed_user_ids(
            values.get("ALLOWED_USER_IDS", "")
        )

        telegram_footer_url = (
            values.get("TELEGRAM_FOOTER_URL", TELEGRAM_LINK).strip() or TELEGRAM_LINK
        )
        instagram_footer_url = (
            values.get("INSTAGRAM_FOOTER_URL", INSTAGRAM_LINK).strip() or INSTAGRAM_LINK
        )
        footer = FooterConfig(
            telegram_url=telegram_footer_url,
            instagram_url=instagram_footer_url,
        )
        auth_db_path = values.get("AUTH_DB_PATH", "auth.sqlite3").strip() or "auth.sqlite3"

        return cls(
            bot_token=bot_token,
            channel_id=_normalize_channel_id(channel_value),
            bot_password=bot_password,
            allowed_user_ids=allowed_user_ids,
            removable_domains=frozenset(
                domain.lower() for domain in _split_values(values, "REMOVABLE_DOMAINS")
            ),
            removable_links=_split_values(values, "REMOVABLE_LINKS"),
            log_level=values.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            telegram_footer_url=footer.telegram_url,
            instagram_footer_url=footer.instagram_url,
            auth_db_path=auth_db_path,
        )

    def is_user_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids

    @property
    def numeric_channel_id(self) -> int | None:
        try:
            return int(self.channel_id)
        except ValueError:
            return None

    def matches_chat(self, chat: Chat) -> bool:
        numeric_channel_id = self.numeric_channel_id
        if numeric_channel_id is not None:
            return chat.id == numeric_channel_id

        return chat.username is not None and f"@{chat.username.lower()}" == self.channel_id


def load_settings() -> Settings:
    load_dotenv()
    return Settings.from_env()
