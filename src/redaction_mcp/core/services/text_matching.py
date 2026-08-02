"""Shared text-target matching rules."""

import re


def compile_text_pattern(values: list[str], *, ignore_case: bool, partial_match: bool) -> re.Pattern[str]:
    """Compile literal text values, optionally allowing substring matches."""
    expression = "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))
    if not partial_match:
        expression = rf"(?<![A-Za-z0-9])(?:{expression})(?![A-Za-z0-9])"
    return re.compile(expression, re.IGNORECASE if ignore_case else 0)
