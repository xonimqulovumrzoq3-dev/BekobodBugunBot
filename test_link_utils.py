from __future__ import annotations

import unittest

from telegram import MessageEntity

from link_utils import (
    FOOTER_SEPARATOR,
    INSTAGRAM_LINK,
    TELEGRAM_FOOTER_LINES,
    TELEGRAM_LINK,
    FooterConfig,
    LinkPolicy,
    SanitizedMessage,
    sanitize_message,
)


def _utf16_offset(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def _entity_text(text: str, entity: MessageEntity) -> str:
    encoded = text.encode("utf-16-le")
    start = entity.offset * 2
    end = (entity.offset + entity.length) * 2
    return encoded[start:end].decode("utf-16-le")


def _footer_entities(result: SanitizedMessage) -> list[MessageEntity]:
    return [
        entity
        for entity in result.entities
        if entity.type == "text_link"
        and entity.url in {TELEGRAM_LINK, INSTAGRAM_LINK}
    ]


class LinkSanitizerTests(unittest.TestCase):
    def test_removes_configured_domains_but_keeps_unrelated_links(self) -> None:
        policy = LinkPolicy(
            removable_domains=frozenset({"t.me", "telegram.me", "instagram.com"})
        )
        text = (
            "Keep https://external.example and remove "
            "https://t.me/old, t.me/second, telegram.me/old, "
            "http://instagram.com/old."
        )

        result = sanitize_message(text, (), policy)

        self.assertIn("https://external.example", result.text)
        self.assertNotIn("t.me/old", result.text)
        self.assertNotIn("t.me/second", result.text)
        self.assertNotIn("telegram.me/old", result.text)
        self.assertNotIn("instagram.com/old", result.text)
        self.assertNotIn(TELEGRAM_LINK, result.text)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
        )

    def test_exact_links_can_be_configured_without_removing_other_paths(self) -> None:
        policy = LinkPolicy(removable_links=("https://t.me/old",))

        result = sanitize_message(
            "https://t.me/old https://t.me/other",
            (),
            policy,
        )

        self.assertNotIn("t.me/old", result.text)
        self.assertIn("https://t.me/other", result.text)
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
        )

    def test_removes_supported_url_forms(self) -> None:
        policy = LinkPolicy(
            removable_domains=frozenset({"t.me", "telegram.me", "instagram.com"})
        )
        text = (
            "https://t.me/a http://telegram.me/b "
            "t.me/c telegram.me/d www.instagram.com/e"
        )

        result = sanitize_message(text, (), policy)

        for link in (
            "t.me/a",
            "telegram.me/b",
            "t.me/c",
            "telegram.me/d",
            "instagram.com/e",
        ):
            self.assertNotIn(link, result.text)

    def test_removes_hidden_text_link_entities(self) -> None:
        entity = MessageEntity(
            type="text_link",
            offset=7,
            length=9,
            url="https://t.me/old",
        )

        result = sanitize_message(
            "Before clickable after",
            (entity,),
            LinkPolicy(removable_domains=frozenset({"t.me"})),
        )

        self.assertNotIn("clickable", result.text)
        self.assertNotIn("https://t.me/old", result.text)
        self.assertFalse(
            any(
                item.type == "text_link" and item.url == "https://t.me/old"
                for item in result.entities
            )
        )

    def test_removes_url_entities(self) -> None:
        url = "https://t.me/old"
        entity = MessageEntity(type="url", offset=4, length=len(url))
        result = sanitize_message(
            f"See {url} now",
            (entity,),
            LinkPolicy(removable_domains=frozenset({"t.me"})),
        )

        self.assertNotIn(url, result.text)
        self.assertFalse(
            any(item.type == "url" and item.length == len(url) for item in result.entities)
        )

    def test_keeps_unrelated_text_link_entity(self) -> None:
        entity = MessageEntity(
            type="text_link",
            offset=7,
            length=10,
            url="https://external.example/page",
        )

        result = sanitize_message(
            "Before click here after",
            (entity,),
            LinkPolicy(removable_domains=frozenset({"t.me"})),
        )

        self.assertIn("click here", result.text)
        kept_entities = [
            item
            for item in result.entities
            if item.type == "text_link" and item.url == "https://external.example/page"
        ]
        self.assertEqual(len(kept_entities), 1)
        self.assertEqual(_entity_text(result.text, kept_entities[0]), "click here")

    def test_preserves_formatting_entities_around_removed_links(self) -> None:
        text = "A https://t.me/old B"
        entity = MessageEntity(
            type="bold",
            offset=0,
            length=_utf16_offset(text, len(text)),
        )

        result = sanitize_message(
            text,
            (entity,),
            LinkPolicy(removable_domains=frozenset({"t.me"})),
        )

        bold_entities = [item for item in result.entities if item.type == "bold"]
        self.assertGreaterEqual(len(bold_entities), 2)
        self.assertNotIn("t.me/old", result.text)

    def test_uses_utf16_offsets_for_emoji(self) -> None:
        url = "https://t.me/old"
        text = f"A😀{url} B"
        entity = MessageEntity(type="url", offset=3, length=len(url))
        result = sanitize_message(
            text,
            (entity,),
            LinkPolicy(removable_domains=frozenset({"t.me"})),
        )

        self.assertTrue(result.text.startswith("A😀"))
        self.assertNotIn(url, result.text)
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
        )

    def test_replaces_legacy_raw_footer_with_clickable_labels(self) -> None:
        text = (
            "Body\n\n"
            f"📱 Telegram: {TELEGRAM_LINK}\n"
            f"📸 Instagram: {INSTAGRAM_LINK}"
        )

        result = sanitize_message(
            text,
            (),
            LinkPolicy(removable_domains=frozenset({"t.me", "instagram.com"})),
        )

        self.assertEqual(result.text, f"Body\n\n{TELEGRAM_FOOTER_LINES[0]}")
        self.assertNotIn(TELEGRAM_LINK, result.text)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
        )

    def test_preserves_unrelated_emoji_and_pipe_in_body(self) -> None:
        result = sanitize_message("Body 😀 | stays", (), LinkPolicy())

        self.assertEqual(
            result.text,
            f"Body 😀 | stays\n\n{TELEGRAM_FOOTER_LINES[0]}",
        )

    def test_preserves_line_break_style_when_replacing_footer(self) -> None:
        result = sanitize_message(
            "Body\r\n\r\n"
            f"📱 Telegram: {TELEGRAM_LINK}\r\n"
            f"📸 Instagram: {INSTAGRAM_LINK}",
            (),
            LinkPolicy(),
        )

        self.assertEqual(
            result.text,
            f"Body\r\n\r\n{TELEGRAM_FOOTER_LINES[0]}",
        )

    def test_uses_existing_line_break_style_for_new_footer(self) -> None:
        result = sanitize_message("Body\r\nMore", (), LinkPolicy())

        self.assertEqual(
            result.text,
            f"Body\r\nMore\r\n\r\n{TELEGRAM_FOOTER_LINES[0]}",
        )

    def test_removes_footer_only_raw_links(self) -> None:
        result = sanitize_message(
            f"{TELEGRAM_LINK} | {INSTAGRAM_LINK}",
            (),
            LinkPolicy(),
        )

        self.assertEqual(result.text, TELEGRAM_FOOTER_LINES[0])
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
        )

    def test_keeps_unlinked_footer_like_body_text(self) -> None:
        text = "Facts\nTelegram | Instagram"

        result = sanitize_message(text, (), LinkPolicy())

        self.assertTrue(result.text.startswith(text))

    def test_keeps_cross_wired_footer_like_content(self) -> None:
        text = f"📸 Instagram: {TELEGRAM_LINK}"

        result = sanitize_message(text, (), LinkPolicy())

        self.assertIn(text, result.text)

    def test_keeps_cross_wired_targets_in_footer_like_line(self) -> None:
        footer = FooterConfig(
            telegram_url="https://t.me/custom",
            instagram_url="https://www.instagram.com/custom",
        )
        text = (
            f"📱 Telegram: {TELEGRAM_LINK} | "
            f"📸 Instagram: {footer.telegram_url}"
        )

        result = sanitize_message(text, (), LinkPolicy(), footer=footer)

        self.assertIn(text, result.text)

    def test_keeps_malformed_label_entity_as_body_content(self) -> None:
        text = "📱 TelegramBot"
        entity = MessageEntity(
            type="text_link",
            offset=_utf16_offset(text, text.index("TelegramBot")),
            length=len("TelegramBot"),
            url=TELEGRAM_LINK,
        )

        result = sanitize_message(text, (entity,), LinkPolicy())

        self.assertIn(text, result.text)
        self.assertTrue(
            any(
                item.type == "text_link" and item.url == TELEGRAM_LINK
                for item in result.entities
            )
        )

    def test_keeps_unrelated_official_text_link_as_body_content(self) -> None:
        text = "Home | Channel"
        entities = (
            MessageEntity(
                type="text_link",
                offset=0,
                length=len("Home"),
                url=TELEGRAM_LINK,
            ),
            MessageEntity(
                type="text_link",
                offset=len("Home | "),
                length=len("Channel"),
                url=INSTAGRAM_LINK,
            ),
        )

        result = sanitize_message(text, entities, LinkPolicy())

        self.assertTrue(result.text.startswith(text))
        self.assertEqual(
            [entity.to_dict() for entity in result.entities],
            [entity.to_dict() for entity in entities],
        )

    def test_keeps_unmatched_icon_link_as_body_content(self) -> None:
        text = "📱 Home"
        entity = MessageEntity(
            type="text_link",
            offset=_utf16_offset(text, text.index("Home")),
            length=len("Home"),
            url=TELEGRAM_LINK,
        )

        result = sanitize_message(text, (entity,), LinkPolicy())

        self.assertIn("📱 Home", result.text)
        self.assertTrue(
            any(
                item.type == "text_link" and item.url == TELEGRAM_LINK
                for item in result.entities
            )
        )

    def test_keeps_single_official_target_as_body_content(self) -> None:
        result = sanitize_message(
            f"Post\n{TELEGRAM_LINK}",
            (),
            LinkPolicy(),
        )

        self.assertIn(TELEGRAM_LINK, result.text)
        self.assertIn("Post", result.text)

    def test_keeps_hidden_single_target_as_body_content(self) -> None:
        text = "Home"
        entity = MessageEntity(
            type="text_link",
            offset=0,
            length=len(text),
            url=TELEGRAM_LINK,
        )

        result = sanitize_message(text, (entity,), LinkPolicy())

        self.assertIn("Home", result.text)
        self.assertTrue(
            any(
                item.type == "text_link" and item.url == TELEGRAM_LINK
                for item in result.entities
            )
        )

    def test_keeps_default_target_raw_line_with_custom_footer(self) -> None:
        result = sanitize_message(
            f"Post\n{TELEGRAM_LINK}",
            (),
            LinkPolicy(),
            footer=FooterConfig(
                telegram_url="https://t.me/custom",
                instagram_url="https://www.instagram.com/custom",
            ),
        )

        self.assertIn(TELEGRAM_LINK, result.text)
        self.assertIn("Post", result.text)

    def test_processed_footer_is_idempotent(self) -> None:
        text = f"Body\n\n{TELEGRAM_FOOTER_LINES[0]}"
        entities = (
            MessageEntity(
                type="text_link",
                offset=_utf16_offset(text, text.index("Telegram")),
                length=len("Telegram"),
                url=TELEGRAM_LINK,
            ),
            MessageEntity(
                type="text_link",
                offset=_utf16_offset(text, text.index("Instagram")),
                length=len("Instagram"),
                url=INSTAGRAM_LINK,
            ),
        )

        result = sanitize_message(text, entities, LinkPolicy())
        repeated = sanitize_message(result.text, result.entities, LinkPolicy())

        self.assertEqual(repeated.text, result.text)
        self.assertEqual(
            [entity.to_dict() for entity in repeated.entities],
            [entity.to_dict() for entity in result.entities],
        )

    def test_does_not_duplicate_existing_official_targets(self) -> None:
        text = f"Existing {TELEGRAM_LINK} {INSTAGRAM_LINK}"
        result = sanitize_message(text, (), LinkPolicy())

        self.assertEqual(result.text, text)
        self.assertEqual(result.text.count(TELEGRAM_LINK), 1)
        self.assertEqual(result.text.count(INSTAGRAM_LINK), 1)

    def test_does_not_duplicate_processed_text_link_footer(self) -> None:
        text = "Hello\n\n📱 Telegram\n📸 Instagram"
        entities = (
            MessageEntity(
                type="text_link",
                offset=_utf16_offset(text, text.index("Telegram")),
                length=len("Telegram"),
                url=TELEGRAM_LINK,
            ),
            MessageEntity(
                type="text_link",
                offset=_utf16_offset(text, text.index("Instagram")),
                length=len("Instagram"),
                url=INSTAGRAM_LINK,
            ),
        )

        result = sanitize_message(text, entities, LinkPolicy())

        self.assertEqual(result.text, f"Hello\n\n{TELEGRAM_FOOTER_LINES[0]}")
        self.assertEqual(len(_footer_entities(result)), 2)

    def test_adds_only_missing_official_footer_line(self) -> None:
        result = sanitize_message(
            f"Existing {TELEGRAM_LINK}",
            (),
            LinkPolicy(),
        )

        self.assertEqual(result.text.count(TELEGRAM_LINK), 1)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertIn("Instagram", result.text)
        self.assertNotIn(FOOTER_SEPARATOR, result.text)
        self.assertEqual(len(_footer_entities(result)), 1)
        self.assertEqual(_footer_entities(result)[0].url, INSTAGRAM_LINK)

    def test_adds_footer_to_a_plain_post(self) -> None:
        result = sanitize_message("Hello", (), LinkPolicy())

        self.assertEqual(
            result.text,
            f"Hello\n\n{TELEGRAM_FOOTER_LINES[0]}",
        )
        self.assertNotIn(TELEGRAM_LINK, result.text)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
        )

    def test_uses_configured_footer_targets(self) -> None:
        result = sanitize_message(
            "Hello 😀",
            (),
            LinkPolicy(),
            footer=FooterConfig(
                telegram_url="https://t.me/custom",
                instagram_url="https://www.instagram.com/custom",
            ),
        )

        self.assertNotIn(TELEGRAM_LINK, result.text)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertEqual(
            [entity.url for entity in result.entities if entity.type == "text_link"],
            ["https://t.me/custom", "https://www.instagram.com/custom"],
        )
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in result.entities if entity.type == "text_link"],
            ["Telegram", "Instagram"],
        )

    def test_respects_utf16_text_and_caption_limits(self) -> None:
        policy = LinkPolicy()
        text_result = sanitize_message("A" * 5000, (), policy, 4096)
        caption_result = sanitize_message("😀" * 1000, (), policy, 1024)

        self.assertLessEqual(
            len(text_result.text.encode("utf-16-le")) // 2,
            4096,
        )
        self.assertLessEqual(
            len(caption_result.text.encode("utf-16-le")) // 2,
            1024,
        )
        self.assertTrue(text_result.text.endswith("Instagram"))
        self.assertTrue(caption_result.text.endswith("Instagram"))
        self.assertEqual(
            [entity.url for entity in _footer_entities(text_result)],
            [TELEGRAM_LINK, INSTAGRAM_LINK],
        )
        self.assertEqual(
            [entity.url for entity in _footer_entities(caption_result)],
            [TELEGRAM_LINK, INSTAGRAM_LINK],
        )


if __name__ == "__main__":
    unittest.main()
