"""Shared literal-text normalization and matching rules."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class TextMatch:
    """A normalized literal match mapped back to the original text."""

    source: str
    start_index: int
    end_index: int

    def start(self) -> int:
        return self.start_index

    def end(self) -> int:
        return self.end_index

    def group(self) -> str:
        return self.source[self.start_index:self.end_index]


def _normalized_with_offsets(value: str) -> tuple[str, list[tuple[int, int]]]:
    normalized: list[str] = []
    offsets: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        end = index + 1
        # Hangul Jamo compose across code points under NFKC, even though the
        # vowel and trailing consonant are not Unicode combining marks.
        first = unicodedata.normalize("NFKC", value[index])
        if len(first) == 1 and end < len(value):
            lead = ord(first)
            next_character = unicodedata.normalize("NFKC", value[end])
            if len(next_character) == 1:
                vowel = ord(next_character)
                if 0x1100 <= lead <= 0x1112 and 0x1161 <= vowel <= 0x1175:
                    end += 1
                elif 0xAC00 <= lead <= 0xD7A3 and (lead - 0xAC00) % 28 == 0:
                    if 0x11A8 <= vowel <= 0x11C2:
                        end += 1
            if end == index + 2 and end < len(value):
                final = unicodedata.normalize("NFKC", value[end])
                if len(final) == 1 and 0x11A8 <= ord(final) <= 0x11C2:
                    end += 1
        while end < len(value) and (
            unicodedata.combining(value[end]) or unicodedata.category(value[end]).startswith("M")
        ):
            end += 1
        for normalized_character in unicodedata.normalize("NFKC", value[index:end]):
            if normalized_character.isspace():
                if normalized and normalized[-1] == " ":
                    offsets[-1] = (offsets[-1][0], end)
                else:
                    normalized.append(" ")
                    offsets.append((index, end))
            else:
                normalized.append(normalized_character)
                offsets.append((index, end))
        index = end
    return "".join(normalized), offsets


def _target_values(values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        normalized, _ = _normalized_with_offsets(value)
        if normalized:
            unique.setdefault(normalized, normalized)
    return sorted(unique, key=len, reverse=True)


def compile_text_pattern(values: list[str], *, ignore_case: bool, partial_match: bool) -> re.Pattern[str]:
    """Compile normalized literals for callers that do not need offsets."""
    expression = "|".join(re.escape(value) for value in _target_values(values))
    if not partial_match:
        expression = rf"(?<![^\W_])(?:{expression})(?![^\W_])"
    return re.compile(expression, re.IGNORECASE if ignore_case else 0)


def find_text_matches(
    source: str, values: list[str], *, ignore_case: bool = False, partial_match: bool = False
) -> Iterator[TextMatch]:
    """Find normalized nonoverlapping literals and return original offsets."""
    normalized, offsets = _normalized_with_offsets(source)
    targets = _target_values(values)
    if not targets or not normalized:
        return
    expression = "|".join(re.escape(value) for value in targets)
    if not partial_match:
        expression = rf"(?<![^\W_])(?:{expression})(?![^\W_])"
    pattern = re.compile(expression, re.IGNORECASE if ignore_case else 0)
    for match in pattern.finditer(normalized):
        start = offsets[match.start()][0]
        end = offsets[match.end() - 1][1]
        yield TextMatch(source, start, end)


def replace_text_matches(
    source: str,
    values: list[str],
    replacement: Callable[[TextMatch], str],
    *,
    ignore_case: bool = False,
    partial_match: bool = False,
) -> tuple[str, int]:
    """Replace normalized literal matches without losing original offsets."""
    matches = list(find_text_matches(source, values, ignore_case=ignore_case, partial_match=partial_match))
    if not matches:
        return source, 0
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.extend((source[cursor:match.start()], replacement(match)))
        cursor = match.end()
    parts.append(source[cursor:])
    return "".join(parts), len(matches)
