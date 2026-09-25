from __future__ import annotations

import unittest

from telegram import MessageEntity

from link_utils import (
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

        self.assertEqual(result.text, "Body\n\n📱 Telegram\n📸 Instagram")
        self.assertNotIn(TELEGRAM_LINK, result.text)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertEqual(
            [_entity_text(result.text, entity) for entity in _footer_entities(result)],
            ["Telegram", "Instagram"],
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

        self.assertEqual(result.text, text)
        self.assertEqual(len(_footer_entities(result)), 2)

    def test_adds_only_missing_official_footer_line(self) -> None:
        result = sanitize_message(
            f"Existing {TELEGRAM_LINK}",
            (),
            LinkPolicy(),
        )

        self.assertEqual(result.text.count(TELEGRAM_LINK), 1)
        self.assertNotIn(INSTAGRAM_LINK, result.text)
        self.assertIn(TELEGRAM_FOOTER_LINES[1], result.text)
        self.assertNotIn(TELEGRAM_FOOTER_LINES[0], result.text)
        self.assertEqual(len(_footer_entities(result)), 1)
        self.assertEqual(_footer_entities(result)[0].url, INSTAGRAM_LINK)

    def test_adds_footer_to_a_plain_post(self) -> None:
        result = sanitize_message("Hello", (), LinkPolicy())

        self.assertEqual(
            result.text,
            f"Hello\n\n{TELEGRAM_FOOTER_LINES[0]}\n{TELEGRAM_FOOTER_LINES[1]}",
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
