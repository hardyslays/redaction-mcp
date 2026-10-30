"""PyMuPDF implementation of the PDF redaction engine."""

from __future__ import annotations

import re
import base64
from collections import Counter
from dataclasses import dataclass
from math import atan2, degrees
from pathlib import Path

import pymupdf as fitz

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.redaction import (
    BoundingBox,
    BoundingBoxTarget,
    PageTarget,
    RedactionOptions,
    RedactionTarget,
    RegexTarget,
    TextTarget,
)
from redaction_mcp.core.services.text_matching import TextMatch, find_text_matches


@dataclass(frozen=True)
class _RenderableCharacter:
    value: str
    rect: fitz.Rect
    quad: fitz.Quad
    origin: fitz.Point
    size: float
    font: str
    flags: int
    color: tuple[float, float, float]
    direction: tuple[float, float]
    text_index: int
    sequence: int


@dataclass(frozen=True)
class _CharacterPosition:
    line: int
    span: int
    rect: fitz.Rect
    quad: fitz.Quad


@dataclass(frozen=True)
class _MatchedRegion:
    quad: fitz.Quad
    text_indices: frozenset[int]
    text_quad: fitz.Quad


def _color(hex_color: str) -> tuple[float, float, float]:
    value = hex_color.lstrip("#")
    if len(value) != 6 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError("fill_color must be a six-digit hex colour, e.g. #000000")
    return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]


def _page(pdf: fitz.Document, number: int) -> fitz.Page:
    if not 0 <= number < pdf.page_count:
        raise ValueError(f"Page {number} is out of range (document has {pdf.page_count} pages)")
    return pdf[number]


def _rect(page: fitz.Page, box: BoundingBox) -> fitz.Rect:
    return fitz.Rect(
        box.x * page.rect.width,
        box.y * page.rect.height,
        (box.x + box.width) * page.rect.width,
        (box.y + box.height) * page.rect.height,
    )


def _quad_from_rect(rect: fitz.Rect) -> fitz.Quad:
    return fitz.Quad(rect.tl, rect.tr, rect.bl, rect.br)


def _tight_char_quad(line_dir: tuple[float, float], span: dict, char: dict) -> fitz.Quad:
    """Return the one-em glyph box instead of PyMuPDF's loose font box."""
    direction = fitz.Point(line_dir)
    length = abs(direction)
    if not length:
        return fitz.recover_char_quad(line_dir, span, char)
    direction /= length
    normal = fitz.Point(direction.y, -direction.x)
    origin = fitz.Point(char["origin"])
    advance = fitz.recover_char_quad(line_dir, span, char).width
    end = origin + direction * advance
    ascender = float(span.get("ascender", 1.0))
    descender = float(span.get("descender", 0.0))
    em = ascender - descender
    if em <= 0:
        return fitz.recover_char_quad(line_dir, span, char)
    upper = float(span["size"]) * ascender / em
    lower = float(span["size"]) * -descender / em
    return fitz.Quad(
        origin + normal * upper,
        end + normal * upper,
        origin - normal * lower,
        end - normal * lower,
    )


def _inset_quad(quad: fitz.Quad, amount: float = 0.5) -> fitz.Quad:
    """Keep matched-text annotations inside their glyph region."""
    along = quad.ur - quad.ul
    across = quad.ll - quad.ul
    if not abs(along) or not abs(across):
        return fitz.Quad(quad)
    amount = min(amount, abs(along) / 4, abs(across) / 4)
    along /= abs(along)
    across /= abs(across)
    return fitz.Quad(
        quad.ul + along * amount + across * amount,
        quad.ur - along * amount + across * amount,
        quad.ll + along * amount - across * amount,
        quad.lr - along * amount - across * amount,
    )


def _intersection_ratio(first: fitz.Rect, second: fitz.Rect) -> float:
    intersection = first & second
    if intersection.is_empty or first.get_area() <= 0:
        return 0.0
    return intersection.get_area() / first.get_area()


def _add(page: fitz.Page, quad: fitz.Quad, color: tuple[float, float, float], options: RedactionOptions) -> None:
    # PyMuPDF removes content using the annotation's bounding rectangle. Draw the
    # direction-aware fill ourselves because PDF redaction appearances are axial.
    annot = page.add_redact_annot(quad, fill=None, cross_out=False)
    if annot:
        annot.update()
    if not options.permanent_redaction:
        _fill(page, quad, color, options.fill_opacity)


def _fill(page: fitz.Page, quad: fitz.Quad, color: tuple[float, float, float], opacity: float) -> None:
    page.draw_quad(quad, color=None, fill=color, fill_opacity=opacity, overlay=True)


def _text_index(page: fitz.Page) -> tuple[str, list[_CharacterPosition | None]]:
    """Return extracted text with a rectangle for every renderable character.

    Raw character boxes let regex redaction cover the matched span itself, rather
    than the entire extracted word. Newlines are retained so multiline patterns
    can match, but have no rectangle of their own.
    """
    text: list[str] = []
    positions: list[_CharacterPosition | None] = []
    line_number = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span_number, span in enumerate(line["spans"]):
                for char in span.get("chars", []):
                    text.append(char["c"])
                    positions.append(
                        _CharacterPosition(
                            line=line_number,
                            span=span_number,
                            rect=fitz.Rect(char["bbox"]),
                            quad=_tight_char_quad(line["dir"], span, char),
                        )
                    )
            text.append("\n")
            positions.append(None)
            line_number += 1
    return "".join(text), positions


def _renderable_characters(page: fitz.Page) -> list[_RenderableCharacter]:
    characters: list[_RenderableCharacter] = []
    sequence = 0
    text_index = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                color_value = span.get("color", 0)
                color = tuple(((color_value >> shift) & 255) / 255 for shift in (16, 8, 0))
                for char in span.get("chars", []):
                    characters.append(
                        _RenderableCharacter(
                            value=char["c"],
                            rect=fitz.Rect(char["bbox"]),
                            quad=_tight_char_quad(line["dir"], span, char),
                            origin=fitz.Point(char["origin"]),
                            size=span["size"],
                            font=span["font"],
                            flags=span.get("flags", 0),
                            color=color,  # type: ignore[arg-type]
                            direction=tuple(line["dir"]),
                            text_index=text_index,
                            sequence=sequence,
                        )
                    )
                    text_index += 1
                sequence += 1
            text_index += 1  # newline inserted by _text_index
    return characters


def _restoration_candidates(
    characters: list[_RenderableCharacter], quad: fitz.Quad, target_indices: frozenset[int]
) -> list[_RenderableCharacter]:
    """Capture every possibly affected source character outside the match."""
    region = quad.rect
    if region.is_empty:
        return []
    return [
        character
        for character in characters
        if character.text_index not in target_indices
        and character.rect.x1 > character.rect.x0
        and character.rect.y1 > character.rect.y0
        and character.rect.x0 < region.x1
        and character.rect.x1 > region.x0
        and character.rect.y0 < region.y1
        and character.rect.y1 > region.y0
    ]


def _base14_fontname(font: str, flags: int) -> str:
    source = font.lower().replace("-", " ")
    bold = bool(flags & 16) or any(token in source for token in ("bold", "black", "semibold", "demi"))
    italic = bool(flags & 2) or any(token in source for token in ("italic", "oblique", "slanted"))
    if "symbol" in source:
        return "symbol"
    if "dingbat" in source:
        return "zapfdingbats"
    if any(token in source for token in ("courier", "mono", "consolas", "menlo", "fixed")):
        family = ("cour", "cobo", "coit", "cobi")
    elif any(token in source for token in (
        "times", "serif", "roman", "georgia", "cambria", "garamond", "baskerville", "palatino",
    )):
        family = ("tiro", "tibo", "tiit", "tibi")
    else:
        family = ("helv", "hebo", "heit", "hebi")
    return family[(2 if italic else 0) + (1 if bold else 0)]


def _restore_characters(
    page: fitz.Page, characters: list[_RenderableCharacter], excluded_indices: set[int]
) -> None:
    unique = {
        character.text_index: character
        for character in characters
        if character.text_index not in excluded_indices
    }
    ordered = [unique[index] for index in sorted(unique)]

    def signature(character: _RenderableCharacter) -> tuple[object, ...]:
        return (
            character.value,
            round(character.origin.x, 2),
            round(character.origin.y, 2),
            round(character.direction[0], 3),
            round(character.direction[1], 3),
            round(character.size, 2),
        )

    # MuPDF's rotated redaction removal does not expose which intersecting
    # glyphs it deleted. Compare the page after apply_redactions() with the
    # captured source and restore only glyphs that are actually absent.
    surviving = Counter(signature(character) for character in _renderable_characters(page))
    missing: list[_RenderableCharacter] = []
    for character in ordered:
        key = signature(character)
        if surviving[key]:
            surviving[key] -= 1
        else:
            missing.append(character)

    runs: list[list[_RenderableCharacter]] = []
    for character in missing:
        if (
            not runs
            or character.sequence != runs[-1][-1].sequence
            or character.text_index != runs[-1][-1].text_index + 1
        ):
            runs.append([character])
        else:
            runs[-1].append(character)

    for run in runs:
        first = run[0]
        angle = degrees(atan2(-first.direction[1], first.direction[0])) % 360
        morph = None if min(angle, 360 - angle) < 0.01 else (first.origin, fitz.Matrix(angle))
        page.insert_text(
            first.origin,
            "".join(character.value for character in run),
            fontsize=first.size,
            fontname=_base14_fontname(first.font, first.flags),
            color=first.color,
            morph=morph,
        )


def _match_regions(
    match: re.Match[str] | TextMatch, positions: list[_CharacterPosition | None]
) -> list[_MatchedRegion]:
    """Merge matched characters by source span while retaining direction and ownership."""
    regions: list[_MatchedRegion] = []
    current_key: tuple[int, int] | None = None
    current: list[tuple[int, _CharacterPosition]] = []

    def flush() -> None:
        if not current:
            return
        first = current[0][1].quad
        last = current[-1][1].quad
        quad = fitz.Quad(first.ul, last.ur, first.ll, last.lr)
        regions.append(_MatchedRegion(_inset_quad(quad), frozenset(index for index, _ in current), quad))

    for index, position in enumerate(positions[match.start():match.end()], start=match.start()):
        if position is None:
            flush()
            current = []
            current_key = None
            continue
        key = (position.line, position.span)
        if current_key is not None and key != current_key:
            flush()
            current = []
        current_key = key
        current.append((index, position))
    flush()
    return regions


def _match_rectangles(
    match: re.Match[str] | TextMatch, positions: list[_CharacterPosition | None]
) -> list[fitz.Rect]:
    """Compatibility helper returning the bounds of direction-aware regions."""
    return [region.quad.rect for region in _match_regions(match, positions)]


def _add_pattern_matches(
    page: fitz.Page, text: str, pattern: re.Pattern[str], positions: list[tuple[int, fitz.Rect] | None], color: tuple[float, float, float],
    options: RedactionOptions, only_first: bool = False,
) -> bool:
    """Add exact-span redactions and return whether at least one match was found."""
    found = False
    for match in pattern.finditer(text):
        if match.start() == match.end():
            continue
        for region in _match_regions(match, positions):
            _add(page, region.quad, color, options)
        found = True
        if only_first:
            return True
    return found


def redact_pdf_document(
    document: Document,
    targets: list[RedactionTarget],
    options: RedactionOptions,
    *,
    data: bytes | None = None,
    source_path: Path | None = None,
    output_path: Path | None = None,
) -> Document:
    """Apply all targets in one pass so permanent redactions are applied safely."""
    if data is None and document.base64data is None:
        raise ValueError("PDF engine requires in-memory document base64data")
    color = _color(options.fill_color)
    pdf = fitz.open(source_path) if source_path is not None else fitz.open(
        stream=data if data is not None else document.decoded_bytes(), filetype="pdf"
    )
    try:
        regex_patterns: dict[int, list[re.Pattern[str]]] = {}
        for target in targets:
            if isinstance(target, BoundingBoxTarget):
                for box in target.values:
                    _page(pdf, box.page)
            elif isinstance(target, PageTarget):
                for number in target.values:
                    _page(pdf, number)
            elif target.pages is not None:
                for number in target.pages:
                    _page(pdf, number)
        for index, target in enumerate(targets):
            if isinstance(target, RegexTarget):
                flags = re.IGNORECASE if target.ignore_case else 0
                if not target.allow_unicode:
                    flags |= re.ASCII
                regex_patterns[index] = [re.compile(pattern, flags) for pattern in target.patterns]

        regex_found: set[int] = set()
        for number in range(pdf.page_count):
            page = pdf[number]
            redactions: list[fitz.Quad] = []
            areas: list[fitz.Rect] = []
            restorations: list[_RenderableCharacter] = []
            target_indices: set[int] = set()
            characters: list[_RenderableCharacter] | None = None
            text_index: tuple[str, list[_CharacterPosition | None]] | None = None

            def add_with_restoration(quad: fitz.Quad, indices: frozenset[int] | None = None) -> None:
                nonlocal characters
                if indices is not None:
                    target_indices.update(indices)
                    if characters is None:
                        characters = _renderable_characters(page)
                    restorations.extend(_restoration_candidates(characters, quad, indices))
                redactions.append(quad)
                _add(page, quad, color, options)

            for index, target in enumerate(targets):
                if isinstance(target, BoundingBoxTarget):
                    for box in target.values:
                        if box.page == number:
                            rect = _rect(page, box)
                            areas.append(rect)
                            add_with_restoration(_quad_from_rect(rect))
                elif isinstance(target, TextTarget):
                    if target.pages is not None and number not in target.pages:
                        continue
                    if text_index is None:
                        text_index = _text_index(page)
                    text, positions = text_index
                    for match in find_text_matches(
                        text,
                        target.values,
                        ignore_case=target.ignore_case,
                        partial_match=target.partial_match,
                    ):
                        for region in _match_regions(match, positions):
                            add_with_restoration(region.quad, region.text_indices)
                elif isinstance(target, PageTarget):
                    if number in target.values:
                        areas.append(page.rect)
                        add_with_restoration(_quad_from_rect(page.rect))
                elif isinstance(target, RegexTarget):
                    if target.only_first_match and index in regex_found:
                        continue
                    if target.pages is not None and number not in target.pages:
                        continue
                    if text_index is None:
                        text_index = _text_index(page)
                    text, positions = text_index
                    for pattern in regex_patterns[index]:
                        for match in pattern.finditer(text):
                            if match.start() == match.end():
                                continue
                            for region in _match_regions(match, positions):
                                add_with_restoration(region.quad, region.text_indices)
                            if target.only_first_match:
                                regex_found.add(index)
                                break
                        if target.only_first_match and index in regex_found:
                            break
            if options.permanent_redaction and redactions:
                page.apply_redactions()
                for quad in redactions:
                    _fill(page, quad, color, options.fill_opacity)
                if restorations:
                    _restore_characters(
                        page,
                        [character for character in restorations
                         if not any(_intersection_ratio(character.rect, area) > 0 for area in areas)],
                        target_indices,
                    )
            del text_index, characters
        if output_path is not None:
            pdf.save(output_path, garbage=4, deflate=True)
            output = b""
        else:
            output = pdf.tobytes(garbage=4, deflate=True)
    finally:
        pdf.close()
    filename = Path(document.filename or "document.pdf")
    return Document(
        base64data=base64.b64encode(output).decode("ascii"),
        mime_type="application/pdf",
        filename=f"{filename.stem}-redacted.pdf",
    )
