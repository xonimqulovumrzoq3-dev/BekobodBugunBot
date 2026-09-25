from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from telegram import MessageEntity

TELEGRAM_LINK = "https://t.me/bekobodbugun"
INSTAGRAM_LINK = "https://www.instagram.com/bekobodbugun"
OFFICIAL_LINKS = (TELEGRAM_LINK, INSTAGRAM_LINK)
TELEGRAM_FOOTER_LABEL = "Telegram"
INSTAGRAM_FOOTER_LABEL = "Instagram"
FOOTER_SEPARATOR = " | "
TELEGRAM_FOOTER_LINES = (
    f"{TELEGRAM_FOOTER_LABEL}{FOOTER_SEPARATOR}{INSTAGRAM_FOOTER_LABEL}",
)
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024

_LINK_ENTITY_TYPES = {"text_link", "url"}
_URL_RE = re.compile(
    r"""
    (?<![\w@])
    (?:
        (?:https?://|tg://)[^\s<>()\[\]{}'"`]+
        |
        (?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+
        [a-z]{2,63}(?:[/?#][^\s<>()\[\]{}'"`]*)?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_TRAILING_PUNCTUATION = ".,!?;:"
_FOOTER_DECORATION_CHARS = frozenset(" \t\r\n|:;,.!?()[]{}–—·•")


def _canonical_url(value: str) -> str | None:
    raw_value = value.strip().strip("<>")
    if not raw_value:
        return None

    if raw_value.lower().startswith("tg://resolve"):
        query = parse_qs(urlsplit(raw_value).query)
        domain = query.get("domain", [""])[0].lstrip("@")
        if domain:
            return f"t.me/{domain.rstrip('/').lower()}"

    if "://" not in raw_value:
        raw_value = f"https://{raw_value}"

    try:
        parsed = urlsplit(raw_value)
        host = parsed.hostname
    except ValueError:
        return None

    if not host:
        return None

    normalized_host = host.lower().rstrip(".").removeprefix("www.")
    path = parsed.path.rstrip("/")
    if not path:
        return normalized_host
    return f"{normalized_host}{path}"


@dataclass(frozen=True, slots=True)
class FooterItem:
    icon: str
    label: str
    url: str

    @property
    def line(self) -> str:
        return self.label


@dataclass(frozen=True, slots=True)
class FooterConfig:
    telegram_url: str = TELEGRAM_LINK
    instagram_url: str = INSTAGRAM_LINK

    def __post_init__(self) -> None:
        telegram_url = self.telegram_url.strip()
        instagram_url = self.instagram_url.strip()
        if _canonical_url(telegram_url) is None or _canonical_url(instagram_url) is None:
            raise ValueError("Footer URLs must be valid non-empty URLs")
        object.__setattr__(self, "telegram_url", telegram_url)
        object.__setattr__(self, "instagram_url", instagram_url)

    def items(self) -> tuple[FooterItem, FooterItem]:
        return (
            FooterItem("📱", TELEGRAM_FOOTER_LABEL, self.telegram_url),
            FooterItem("📸", INSTAGRAM_FOOTER_LABEL, self.instagram_url),
        )


def _normalize_domain(value: str) -> str | None:
    canonical = _canonical_url(value)
    if canonical is None:
        return None
    return canonical.removeprefix("*.").split("/", 1)[0]


@dataclass(frozen=True, slots=True)
class LinkPolicy:
    removable_domains: frozenset[str] = frozenset()
    removable_links: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_domains = frozenset(
            domain
            for value in self.removable_domains
            if (domain := _normalize_domain(value)) is not None
        )
        normalized_links = tuple(
            canonical
            for value in self.removable_links
            if (canonical := _canonical_url(value)) is not None
        )
        object.__setattr__(self, "removable_domains", normalized_domains)
        object.__setattr__(self, "removable_links", normalized_links)

    def should_remove(self, value: str) -> bool:
        canonical = _canonical_url(value)
        if canonical is None:
            return False

        host = canonical.split("/", 1)[0]
        if any(
            host == domain or host.endswith(f".{domain}")
            for domain in self.removable_domains
        ):
            return True
        return canonical in self.removable_links


@dataclass(frozen=True, slots=True)
class SanitizedMessage:
    text: str
    entities: tuple[MessageEntity, ...]


@dataclass(frozen=True, slots=True)
class _FooterLink:
    start: int
    end: int
    canonical: str
    visible: str
    is_raw: bool


def _url_candidate(match: re.Match[str]) -> str:
    candidate = match.group(0)
    while candidate and candidate[-1] in _TRAILING_PUNCTUATION:
        candidate = candidate[:-1]
    return candidate


def _url_ranges(text: str) -> Iterable[tuple[int, int, str]]:
    for match in _URL_RE.finditer(text):
        candidate = _url_candidate(match)
        if candidate:
            start = match.start()
            yield start, start + len(candidate), candidate


def _footer_item_target_keys(item: FooterItem) -> set[str]:
    default_url = (
        TELEGRAM_LINK
        if item.label == TELEGRAM_FOOTER_LABEL
        else INSTAGRAM_LINK
    )
    return {
        canonical
        for value in (item.url, default_url)
        if (canonical := _canonical_url(value)) is not None
    }


def _footer_target_keys(
    footer: FooterConfig,
    *,
    labels: set[str] | None = None,
    include_defaults: bool,
) -> set[str]:
    items: tuple[FooterItem, ...] = footer.items()
    if labels is not None:
        items = tuple(item for item in items if item.label in labels)
    keys = {
        canonical
        for item in items
        if (canonical := _canonical_url(item.url)) is not None
    }
    if include_defaults:
        for item in items:
            keys.update(_footer_item_target_keys(item))
    return keys


def _footer_link_ranges(
    text: str,
    entities: Sequence[MessageEntity],
    keys: set[str],
    start: int,
    end: int,
) -> tuple[list[_FooterLink], set[str]]:
    ranges: list[_FooterLink] = []
    present: set[str] = set()
    for range_start, range_end, candidate in _url_ranges(text):
        if range_end <= start or range_start >= end:
            continue
        canonical = _canonical_url(candidate)
        if canonical in keys:
            ranges.append(
                _FooterLink(
                    max(start, range_start),
                    min(end, range_end),
                    canonical,
                    candidate,
                    True,
                )
            )
            present.add(canonical)
    for entity in entities:
        if entity.type not in _LINK_ENTITY_TYPES:
            continue
        target = _entity_target(text, entity)
        canonical = _canonical_url(target) if target is not None else None
        if canonical not in keys:
            continue
        range_start, range_end = _entity_range(text, entity)
        if range_end <= start or range_start >= end:
            continue
        visible_start = max(start, range_start)
        visible_end = min(end, range_end)
        ranges.append(
            _FooterLink(
                visible_start,
                visible_end,
                canonical,
                text[visible_start:visible_end],
                False,
            )
        )
        present.add(canonical)
    return ranges, present


def _footer_marker_intervals(
    text: str,
    footer: FooterConfig,
    start: int,
    end: int,
) -> tuple[list[tuple[int, int]], set[str], set[str]]:
    intervals: list[tuple[int, int]] = []
    labels: set[str] = set()
    matched_labels: set[str] = set()
    url_ranges = [
        (range_start, range_end)
        for range_start, range_end, _ in _url_ranges(text)
        if range_end > start and range_start < end
    ]
    for item in footer.items():
        icon_matches = [
            match
            for match in re.finditer(
                re.escape(item.icon),
                text[start:end],
                re.IGNORECASE,
            )
            if not any(
                start + match.start() < range_end
                and start + match.end() > range_start
                for range_start, range_end in url_ranges
            )
        ] if item.icon else []
        label_matches = [
            match
            for match in re.finditer(
                rf"(?<!\w){re.escape(item.label)}(?!\w)",
                text[start:end],
                re.IGNORECASE,
            )
            if not any(
                start + match.start() < range_end
                and start + match.end() > range_start
                for range_start, range_end in url_ranges
            )
        ]
        for match in [*icon_matches, *label_matches]:
            intervals.append((start + match.start(), start + match.end()))
        if label_matches:
            labels.add(item.label)
        if icon_matches and label_matches:
            matched_labels.add(item.label)
    return intervals, labels, matched_labels


def _footer_fragment_is_valid(
    text: str,
    start: int,
    end: int,
    intervals: Sequence[tuple[int, int]],
) -> bool:
    cursor = start
    for interval_start, interval_end in _merge_ranges(intervals):
        interval_start = max(start, interval_start)
        interval_end = min(end, interval_end)
        if interval_end <= interval_start:
            continue
        if any(
            character not in _FOOTER_DECORATION_CHARS
            for character in text[cursor:interval_start]
        ):
            return False
        cursor = max(cursor, interval_end)
    return all(
        character in _FOOTER_DECORATION_CHARS
        for character in text[cursor:end]
    )


def _footer_link_matches_item(link: _FooterLink, item: FooterItem) -> bool:
    if link.canonical not in _footer_item_target_keys(item):
        return False
    if link.is_raw:
        return True
    if _canonical_url(link.visible.strip()) == link.canonical:
        return True

    intervals: list[tuple[int, int]] = []
    if item.icon:
        intervals.extend(
            (match.start(), match.end())
            for match in re.finditer(
                re.escape(item.icon),
                link.visible,
                re.IGNORECASE,
            )
        )
    intervals.extend(
        (match.start(), match.end())
        for match in re.finditer(
            rf"(?<!\w){re.escape(item.label)}(?!\w)",
            link.visible,
            re.IGNORECASE,
        )
    )
    if not intervals:
        return False

    cursor = 0
    for interval_start, interval_end in _merge_ranges(intervals):
        if any(
            character not in _FOOTER_DECORATION_CHARS
            for character in link.visible[cursor:interval_start]
        ):
            return False
        cursor = max(cursor, interval_end)
    return all(
        character in _FOOTER_DECORATION_CHARS
        for character in link.visible[cursor:]
    )


def _line_footer_marker(
    text: str,
    entities: Sequence[MessageEntity],
    footer: FooterConfig,
    line_start: int,
    line_end: int,
) -> int | None:
    marker_intervals, labels, matched_labels = _footer_marker_intervals(
        text,
        footer,
        line_start,
        line_end,
    )
    target_keys = _footer_target_keys(
        footer,
        labels=labels or None,
        include_defaults=bool(labels),
    )
    link_records, present = _footer_link_ranges(
        text,
        entities,
        target_keys,
        line_start,
        line_end,
    )
    link_ranges = [(link.start, link.end) for link in link_records]
    if not marker_intervals:
        if len(present) < 2:
            return None
        if not all(
            link.is_raw or _canonical_url(link.visible.strip()) == link.canonical
            for link in link_records
        ):
            return None
        if not _footer_fragment_is_valid(text, line_start, line_end, link_ranges):
            return None
        return line_start

    if not labels or not link_ranges:
        return None
    if not all(
        any(
            _footer_link_matches_item(link, item)
            for item in footer.items()
        )
        for link in link_records
    ):
        return None
    for item in footer.items():
        if item.label not in labels:
            continue
        if not any(
            _footer_link_matches_item(link, item)
            for link in link_records
        ):
            return None
    if len(labels) > 1 and len(present) < 2:
        return None
    if len(present) < 2 and not matched_labels:
        return None
    intervals = _merge_ranges([*marker_intervals, *link_ranges])
    marker = min(interval_start for interval_start, _ in intervals)
    if not _footer_fragment_is_valid(text, marker, line_end, intervals):
        return None
    if any(
        character not in _FOOTER_DECORATION_CHARS
        for character in text[line_start:marker]
    ):
        return None
    return marker


def _footer_ranges(
    text: str,
    entities: Sequence[MessageEntity],
    footer: FooterConfig,
) -> list[tuple[int, int]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    records: list[tuple[int, int, int]] = []
    offset = 0
    for line in lines:
        end = offset + len(line)
        content_end = offset + len(line.rstrip("\r\n"))
        records.append((offset, end, content_end))
        offset = end

    matches: dict[int, int] = {}
    for index, (start, end, content_end) in enumerate(records):
        marker = _line_footer_marker(
            text,
            entities,
            footer,
            start,
            content_end,
        )
        if marker is not None:
            matches[index] = marker

    last_content = next(
        (
            index
            for index in range(len(records) - 1, -1, -1)
            if text[records[index][0] : records[index][2]].strip()
        ),
        None,
    )
    if last_content is None or last_content not in matches:
        return []

    selected: list[tuple[int, int]] = []
    index = last_content
    while index >= 0:
        start, _, _ = records[index]
        if index in matches:
            selected.append((index, matches[index]))
        elif text[start : records[index][2]].strip():
            break
        index -= 1
    selected.reverse()
    if not selected:
        return []

    first_index, _ = selected[0]
    first_start, _, _ = records[first_index]
    return [(first_start, len(text))]


# Telegram entity offsets and lengths are measured in UTF-16 code units.
def _utf16_to_index(text: str, offset: int) -> int:
    current_offset = 0
    for index, character in enumerate(text):
        if current_offset >= offset:
            return index
        current_offset += 2 if ord(character) > 0xFFFF else 1
    return len(text)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _utf16_offset(text: str, index: int) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text[:index])


def _prefix_index(text: str, limit: int) -> int:
    if limit <= 0:
        return 0
    if _utf16_length(text) <= limit:
        return len(text)

    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _utf16_length(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def _entity_range(text: str, entity: MessageEntity) -> tuple[int, int]:
    start = _utf16_to_index(text, entity.offset)
    end = _utf16_to_index(text, entity.offset + entity.length)
    return start, end


def _entity_target(text: str, entity: MessageEntity) -> str | None:
    if entity.type == "text_link":
        return entity.url
    if entity.type == "url":
        start, end = _entity_range(text, entity)
        return text[start:end]
    return None


def _entity_is_removable(
    text: str,
    entity: MessageEntity,
    policy: LinkPolicy,
) -> bool:
    target = _entity_target(text, entity)
    return target is not None and policy.should_remove(target)


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract_removals(
    start: int,
    end: int,
    removals: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    segments = [(start, end)]
    for removal_start, removal_end in removals:
        next_segments: list[tuple[int, int]] = []
        for segment_start, segment_end in segments:
            if removal_end <= segment_start or removal_start >= segment_end:
                next_segments.append((segment_start, segment_end))
                continue
            if segment_start < removal_start:
                next_segments.append((segment_start, removal_start))
            if removal_end < segment_end:
                next_segments.append((removal_end, segment_end))
        segments = next_segments
    return segments


def _copy_entity(
    entity: MessageEntity,
    offset: int,
    length: int,
) -> MessageEntity:
    # PTB entities are immutable, so reconstruct them with adjusted offsets.
    entity_data = entity.to_dict()
    entity_data["offset"] = offset
    entity_data["length"] = length
    return MessageEntity.de_list([entity_data])[0]


def _map_index_after_removals(index: int, removals: Sequence[tuple[int, int]]) -> int:
    removed_before = 0
    for start, end in removals:
        if end <= index:
            removed_before += end - start
        elif start < index:
            removed_before += index - start
    return index - removed_before


def _remove_ranges(
    text: str,
    entities: Sequence[MessageEntity],
    removals: Sequence[tuple[int, int]],
    policy: LinkPolicy,
) -> tuple[str, tuple[MessageEntity, ...]]:
    merged_removals = _merge_ranges(removals)
    if not merged_removals:
        return text, tuple(entities)

    chunks: list[str] = []
    cursor = 0
    for start, end in merged_removals:
        chunks.append(text[cursor:start])
        cursor = end
    chunks.append(text[cursor:])
    new_text = "".join(chunks)

    new_entities: list[MessageEntity] = []
    for entity in entities:
        if _entity_is_removable(text, entity, policy):
            continue

        start, end = _entity_range(text, entity)
        for segment_start, segment_end in _subtract_removals(
            start,
            end,
            merged_removals,
        ):
            if segment_end <= segment_start:
                continue
            mapped_start = _map_index_after_removals(segment_start, merged_removals)
            mapped_end = _map_index_after_removals(segment_end, merged_removals)
            new_start = _utf16_offset(new_text, mapped_start)
            new_end = _utf16_offset(new_text, mapped_end)
            new_entity = _copy_entity(entity, new_start, new_end - new_start)
            if new_entity.length > 0:
                new_entities.append(new_entity)

    return new_text, tuple(new_entities)


def _official_link_keys(
    text: str,
    entities: Sequence[MessageEntity],
    footer: FooterConfig,
) -> set[str]:
    official_keys = {
        canonical
        for item in footer.items()
        if (canonical := _canonical_url(item.url)) is not None
    }
    present: set[str] = set()
    for _, _, candidate in _url_ranges(text):
        canonical = _canonical_url(candidate)
        if canonical in official_keys:
            present.add(canonical)
    for entity in entities:
        if entity.type not in _LINK_ENTITY_TYPES:
            continue
        target = _entity_target(text, entity)
        if target is None:
            continue
        canonical = _canonical_url(target)
        if canonical in official_keys:
            present.add(canonical)
    return present


def _footer_separator(body: str) -> str:
    if not body or re.search(r"(?:\r\n|\r|\n){2}$", body):
        return ""
    if body.endswith(("\r\n", "\n")):
        return "\n" if body.endswith("\n") and not body.endswith("\r\n") else "\r\n"
    if body.endswith("\r"):
        return "\r"
    line_ending = re.search(r"\r\n|\r|\n", body)
    return (line_ending.group(0) if line_ending else "\n") * 2


def _footer_text_and_entities(
    body: str,
    entities: Sequence[MessageEntity],
    missing_items: Sequence[FooterItem],
) -> tuple[str, tuple[MessageEntity, ...]]:
    if not missing_items:
        return body, tuple(entities)

    separator = _footer_separator(body)

    footer = FOOTER_SEPARATOR.join(item.label for item in missing_items)
    combined = f"{body}{separator}{footer}"
    combined_entities = list(entities)
    footer_start = len(body) + len(separator)
    footer_cursor = footer_start
    for index, item in enumerate(missing_items):
        label_start = footer_cursor
        combined_entities.append(
            MessageEntity(
                type="text_link",
                offset=_utf16_offset(combined, label_start),
                length=_utf16_length(item.label),
                url=item.url,
            )
        )
        footer_cursor += len(item.label)
        if index < len(missing_items) - 1:
            footer_cursor += len(FOOTER_SEPARATOR)
    return combined, tuple(combined_entities)


def _truncate_with_entities(
    text: str,
    entities: Sequence[MessageEntity],
    limit: int,
) -> tuple[str, tuple[MessageEntity, ...]]:
    if _utf16_length(text) <= limit:
        return text, tuple(entities)
    if limit <= 0:
        return "", ()

    prefix_limit = max(0, limit - 1)
    prefix_end = _prefix_index(text, prefix_limit)
    kept_text = text[:prefix_end].rstrip()
    if not kept_text:
        return "…", ()

    new_text = f"{kept_text}…"
    new_entities: list[MessageEntity] = []
    for entity in entities:
        start, end = _entity_range(text, entity)
        end = min(end, len(kept_text))
        if end <= start:
            continue
        new_start = _utf16_offset(new_text, start)
        new_end = _utf16_offset(new_text, end)
        new_entity = _copy_entity(entity, new_start, new_end - new_start)
        if new_entity.length > 0:
            new_entities.append(new_entity)
    return new_text, tuple(new_entities)


def sanitize_message(
    text: str,
    entities: Sequence[MessageEntity],
    policy: LinkPolicy,
    max_length: int = TELEGRAM_TEXT_LIMIT,
    footer: FooterConfig | None = None,
) -> SanitizedMessage:
    footer_config = footer or FooterConfig()
    removal_ranges = _footer_ranges(text, entities, footer_config)
    removal_ranges.extend(
        (start, end)
        for start, end, candidate in _url_ranges(text)
        if policy.should_remove(candidate)
    )
    for entity in entities:
        if _entity_is_removable(text, entity, policy):
            start, end = _entity_range(text, entity)
            removal_ranges.append((start, end))

    body, body_entities = _remove_ranges(text, entities, removal_ranges, policy)
    for _ in range(4):
        present_keys = _official_link_keys(body, body_entities, footer_config)
        missing_items = tuple(
            item
            for item in footer_config.items()
            if _canonical_url(item.url) not in present_keys
        )
        combined, combined_entities = _footer_text_and_entities(
            body,
            body_entities,
            missing_items,
        )
        if _utf16_length(combined) <= max_length:
            return SanitizedMessage(combined, combined_entities)

        footer_length = _utf16_length(combined) - _utf16_length(body)
        body, body_entities = _truncate_with_entities(
            body,
            body_entities,
            max(0, max_length - footer_length),
        )

    present_keys = _official_link_keys(body, body_entities, footer_config)
    missing_items = tuple(
        item
        for item in footer_config.items()
        if _canonical_url(item.url) not in present_keys
    )
    footer_text, footer_entities = _footer_text_and_entities(
        "",
        (),
        missing_items,
    )
    if _utf16_length(footer_text) > max_length:
        footer_text, footer_entities = _truncate_with_entities(
            footer_text,
            footer_entities,
            max_length,
        )
    return SanitizedMessage(footer_text, footer_entities)


__all__ = [
    "FOOTER_SEPARATOR",
    "INSTAGRAM_LINK",
    "OFFICIAL_LINKS",
    "TELEGRAM_CAPTION_LIMIT",
    "TELEGRAM_FOOTER_LINES",
    "TELEGRAM_LINK",
    "TELEGRAM_TEXT_LIMIT",
    "FooterConfig",
    "FooterItem",
    "LinkPolicy",
    "SanitizedMessage",
    "sanitize_message",
]
