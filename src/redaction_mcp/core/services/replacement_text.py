"""Replacement value generation shared by document-specific engines."""

from __future__ import annotations

from collections.abc import Callable
from calendar import monthrange
from datetime import datetime
from functools import lru_cache
import hashlib
import re
import unicodedata

from faker import Faker

from redaction_mcp.core.models.replacement import ReplacementTarget


_STREET_SUFFIX = (
    r"(?:alley|aly|avenue|ave|boulevard|blvd|circle|cir|court|ct|drive|dr|"
    r"highway|hwy|lane|ln|parkway|pkwy|place|pl|road|rd|street|st|terrace|ter|trail|trl|way)"
)
_ADDRESS_PATTERN = re.compile(
    rf"^(?:\d{{1,6}}[A-Za-z]?\s+.+?\b{_STREET_SUFFIX}\b|P\.?\s*O\.?\s+Box\s+\d+)(?:\s+.+)?$",
    re.IGNORECASE | re.DOTALL,
)


def _partial(value: str) -> str:
    visible = [index for index, char in enumerate(value) if not char.isspace()]
    keep = 4 if len(visible) > 8 else 2
    protected = set(visible[-keep:])
    return "".join(char if char.isspace() or index in protected else "*" for index, char in enumerate(value))


def _shape_dummy(value: str) -> str:
    return "".join(
        "x" if char.islower() else "X" if char.isupper() or char.isalpha()
        else "0" if char.isdigit() else char
        for char in value
    )


def _shape_from_candidate(value: str, candidate: str) -> str:
    """Keep source separators and character classes while using generated data."""
    letters = iter(character for character in candidate if character.isalpha())
    digits = iter(character for character in candidate if character.isdigit())
    shaped = "".join(
        (next(letters, "x").upper() if character.isupper() else next(letters, "x").lower())
        if character.isalpha() else next(digits, "0") if character.isdigit() else character
        for character in value
    )
    return _regex_dummy(value) if shaped == value else shaped


def _regex_dummy(value: str) -> str:
    generated: list[str] = []
    for char in value:
        if "a" <= char <= "z":
            generated.append(chr((ord(char) - ord("a") + 1) % 26 + ord("a")))
        elif "A" <= char <= "Z":
            generated.append(chr((ord(char) - ord("A") + 1) % 26 + ord("A")))
        elif "0" <= char <= "9":
            generated.append(str((int(char) + 1) % 10))
        elif char.isalpha():
            ascii_letters = [letter for letter in unicodedata.normalize("NFKD", char) if letter.isascii() and letter.isalpha()]
            if ascii_letters:
                letter = ascii_letters[0].upper() if char.isupper() else ascii_letters[0].lower()
                generated.append(_regex_dummy(letter))
            else:
                generated.append("X" if char.isupper() or not char.islower() else "x")
        elif char.isdigit():
            try:
                generated.append(str((unicodedata.digit(char) + 1) % 10))
            except ValueError:
                generated.append("0")
        else:
            generated.append(char)
    return "".join(generated)


def _rule_entity_type(value: str) -> str | None:
    stripped = value.strip()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", stripped):
        return "EMAIL"
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", stripped):
        return "SSN"
    if re.fullmatch(r"(?:\d[ -]?){13,19}", stripped):
        return "CREDIT_CARD"
    if re.fullmatch(r"\d{5}(?:-\d{4})?", stripped):
        return "ZIPCODE"
    if re.fullmatch(r"[+()\d .-]{7,}", stripped) and sum(char.isdigit() for char in stripped) >= 7:
        return "PHONE"
    for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            datetime.strptime(stripped, date_format)
            return "DATE"
        except ValueError:
            pass
    if _ADDRESS_PATTERN.fullmatch(stripped):
        return "ADDRESS"
    return None


@lru_cache(maxsize=1)
def _english_pipeline():
    """Load the optional English model once per process."""
    try:
        import en_core_web_sm  # type: ignore[import-not-found]
        return en_core_web_sm.load(disable=["tagger", "parser", "lemmatizer"])
    except (ImportError, OSError, RuntimeError):
        return None


def _model_entity_type(value: str) -> str | None:
    """Use an installed English spaCy pipeline without downloading at runtime."""
    pipeline = _english_pipeline()
    if pipeline is None:
        return None
    try:
        document = pipeline(value)
    except (OSError, RuntimeError):
        return None
    labels = {entity.label_ for entity in document.ents if entity.text.strip() == value.strip()}
    if "PERSON" in labels:
        return "NAME"
    if labels & {"GPE", "LOC", "FAC"}:
        return "PLACE"
    return None


def infer_entity_type(value: str) -> str:
    return _rule_entity_type(value) or _model_entity_type(value) or "GENERIC"


def _case_like(value: str, replacement: str) -> str:
    letters = [character for character in value if character.isalpha()]
    if letters and all(character.isupper() for character in letters):
        return replacement.upper()
    if letters and all(character.islower() for character in letters):
        return replacement.lower()
    if value.istitle():
        return replacement.title()
    return replacement


def _preserve_separators(value: str, digits: str) -> str:
    iterator = iter(character for character in digits if character.isdigit())
    return "".join(next(iterator, "0") if character.isdigit() else character for character in value)


def _preserve_date(value: str, fake: Faker) -> str:
    for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            source_date = datetime.strptime(value.strip(), date_format)
        except ValueError:
            continue

        def format_date(generated_date) -> str:
            generated = generated_date.strftime(date_format)
            if "%B" in date_format or "%b" in date_format:
                if re.search(r"\s\d,", value):
                    generated = re.sub(r"\s0(\d,)", r" \1", generated)
            else:
                source_parts = re.split(r"([/-])", value.strip())
                generated_parts = re.split(r"([/-])", generated)
                generated = "".join(
                    part.lstrip("0") if source_part.isdigit() and len(source_part) == 1 and len(part) == 2
                    else part
                    for source_part, part in zip(source_parts, generated_parts)
                )
            return generated

        candidates: list[str] = []
        for _ in range(32):
            generated = format_date(fake.date_between(start_date="-30y", end_date="today"))
            candidates.append(generated)
            if len(generated) == len(value):
                return generated
        # Short month names and unpadded numeric dates can be rare among random
        # candidates. Keep their shape by changing the year of the source date.
        year = fake.date_between(start_date="-30y", end_date="today").year
        if year == source_date.year:
            year -= 1
        day = min(source_date.day, monthrange(year, source_date.month)[1])
        same_shape = source_date.replace(year=year, day=day)
        candidates.append(format_date(same_shape))
        return min(candidates, key=lambda item: abs(len(item) - len(value)))
    return _shape_dummy(value)


def _fit(value: str, candidate: str) -> str:
    tolerance = max(1, round(len(value) * 0.15))
    lower, upper = max(1, len(value) - tolerance), len(value) + tolerance
    if len(candidate) > upper:
        candidate = candidate[:upper].rstrip()
    elif len(candidate) < lower:
        candidate += "x" * (lower - len(candidate))
    return _case_like(value, candidate) if lower <= len(candidate) <= upper else _shape_dummy(value)


def _shape_conflicts(value: str, candidate: str) -> bool:
    for index, character in enumerate(value):
        if index >= len(candidate):
            return True
        generated = candidate[index]
        if character.isdigit() and not generated.isdigit():
            return True
        if character.isupper() and not generated.isupper():
            return True
        if character.islower() and not generated.islower():
            return True
        if not character.isalnum() and character != generated:
            return True
    return False


def _closest_generated(generator: Callable[[], str], length: int, attempts: int = 16) -> str:
    values = [generator() for _ in range(attempts)]
    return min(values, key=lambda candidate: abs(len(candidate) - length))


def _semantic_address(value: str, fake: Faker) -> str:
    """Generate an address with the same physical line structure as the source."""
    source_lines = value.splitlines() or [value]
    newline = "\r\n" if "\r\n" in value else "\r" if "\r" in value else "\n"

    if len(source_lines) > 1:
        generated = [_closest_generated(fake.street_address, len(source_lines[0]))]
        for source_line in source_lines[1:]:
            if re.search(r"\d", source_line) or "," in source_line:
                locality = f"{fake.city()}, {fake.state_abbr()} {fake.postcode()}"
            else:
                locality = _closest_generated(fake.city, len(source_line))
            generated.append(locality)
        return _case_like(value, newline.join(generated))

    source = source_lines[0]
    street_match = re.match(
        rf"^(?:\d{{1,6}}[A-Za-z]?\s+.+?\b{_STREET_SUFFIX}\b|P\.?\s*O\.?\s+Box\s+\d+)\s*(.*)$",
        source,
        re.IGNORECASE,
    )
    has_locality = bool(street_match and street_match.group(1).strip())
    street_candidates = [fake.street_address() for _ in range(16)]
    if not has_locality:
        return _case_like(value, min(street_candidates, key=lambda item: abs(len(item) - len(source))))
    city_candidates = [fake.city() for _ in range(16)]
    combinations = (f"{street} {city}" for street in street_candidates for city in city_candidates)
    return _case_like(value, min(combinations, key=lambda item: abs(len(item) - len(source))))


def _semantic_name(value: str, fake: Faker) -> str:
    words = iter((fake.first_name, fake.last_name))

    def replace_word(match: re.Match[str]) -> str:
        generator = next(words, fake.last_name)
        candidate = _closest_generated(generator, len(match.group()))
        fitted = candidate[:len(match.group())].ljust(len(match.group()), "x")
        result = _case_like(match.group(), fitted)
        return _regex_dummy(match.group()) if result == match.group() else result

    return re.sub(r"[^\W\d_]+", replace_word, value)


def _semantic(value: str, entity_type: str, fake: Faker) -> str:
    kind = entity_type.upper().replace(" ", "_").replace("-", "_")
    if kind in {"DATE", "DATETIME"}:
        return _preserve_date(value, fake)
    if kind in {"PHONE", "PHONE_NUMBER"}:
        return _preserve_separators(value, fake.msisdn())
    if kind in {"SSN", "SOCIAL_SECURITY_NUMBER"}:
        return _preserve_separators(value, fake.ssn())
    if kind in {"CREDIT_CARD", "CREDIT_CARD_NUMBER"}:
        return _preserve_separators(value, fake.credit_card_number())
    if kind in {"ZIP", "ZIPCODE", "POSTAL_CODE"}:
        return _preserve_separators(value, fake.postcode())
    if kind == "ADDRESS":
        candidate = _semantic_address(value, fake)
        tolerance = max(1, round(len(value) * 0.15))
        if abs(len(candidate) - len(value)) <= tolerance:
            return candidate
        shaped = _shape_from_candidate(value, candidate)
        characters = list(shaped)
        for match in re.finditer(rf"\b{_STREET_SUFFIX}\b", value, re.IGNORECASE):
            characters[match.start():match.end()] = value[match.start():match.end()]
        return "".join(characters)
    if kind in {"NAME", "PERSON"}:
        return _semantic_name(value, fake)
    generators = {
        "NAME": fake.name,
        "PERSON": fake.name,
        "EMAIL": fake.email,
        "PLACE": fake.city,
        "LOCATION": fake.city,
        "CITY": fake.city,
        "COMPANY": fake.company,
    }
    generator = generators.get(kind)
    candidate = generator().replace("\n", ", ") if generator else _shape_dummy(value)
    fitted = _fit(value, candidate)
    if _shape_conflicts(value, fitted) or (
        kind == "EMAIL" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fitted)
    ):
        return _shape_from_candidate(value, candidate)
    return fitted


class ReplacementContext:
    """Keep semantic replacements stable for one document or job."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def replacement_text(self, original: str, target: ReplacementTarget) -> str:
        if target.replacement_type == "PARTIAL":
            return _partial(original)
        if target.replacement_type == "STATIC":
            return f"[{target.static_text}]"
        if target.replacement_type == "REGEX":
            return _regex_dummy(original)
        entity_type = target.entity_type or infer_entity_type(original)
        key = (entity_type.upper(), original.casefold())
        if key not in self._values:
            seed = int.from_bytes(hashlib.sha256("\0".join(key).encode()).digest()[:8], "big")
            fake = Faker("en_US")
            fake.seed_instance(seed)
            self._values[key] = _semantic(original, entity_type, fake)
        result = _case_like(original, self._values[key])
        return _regex_dummy(original) if result == original else result


def replacement_text(original: str, target: ReplacementTarget) -> str:
    """Build one replacement for backwards-compatible direct callers."""
    return ReplacementContext().replacement_text(original, target)
