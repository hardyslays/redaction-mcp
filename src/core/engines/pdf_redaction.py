"""PyMuPDF implementation of the PDF redaction engine."""

from __future__ import annotations

import re
import base64
from pathlib import Path

import fitz

from src.core.models.document import Document
from src.core.models.redaction import (
    BoundingBox,
    BoundingBoxTarget,
    PageTarget,
    RedactionOptions,
    RedactionTarget,
    RegexTarget,
    TextTarget,
)


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
    if box.units == "normalized":
        return fitz.Rect(box.x * page.rect.width, box.y * page.rect.height,
                         (box.x + box.width) * page.rect.width, (box.y + box.height) * page.rect.height)
    scale = 72 if box.units == "inches" else 1
    return fitz.Rect(box.x * scale, box.y * scale, (box.x + box.width) * scale, (box.y + box.height) * scale)


def _add(page: fitz.Page, rect: fitz.Rect, color: tuple[float, float, float], options: RedactionOptions) -> None:
    annot = page.add_redact_annot(rect, fill=color)
    if annot and options.fill_opacity < 1:
        annot.set_opacity(options.fill_opacity)
        annot.update()


def _pages(pdf: fitz.Document, selection: list[int] | None) -> list[fitz.Page]:
    return [_page(pdf, page_number) for page_number in (selection if selection is not None else range(pdf.page_count))]


def _text_index(page: fitz.Page) -> tuple[str, list[tuple[int, fitz.Rect] | None]]:
    """Return extracted text with a rectangle for every renderable character.

    Raw character boxes let regex redaction cover the matched span itself, rather
    than the entire extracted word. Newlines are retained so multiline patterns
    can match, but have no rectangle of their own.
    """
    text: list[str] = []
    positions: list[tuple[int, fitz.Rect] | None] = []
    line_number = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span.get("chars", []):
                    text.append(char["c"])
                    positions.append((line_number, fitz.Rect(char["bbox"])))
            text.append("\n")
            positions.append(None)
            line_number += 1
    return "".join(text), positions


def _match_rectangles(match: re.Match[str], positions: list[tuple[int, fitz.Rect] | None]) -> list[fitz.Rect]:
    """Merge adjacent matched characters on each line into minimal redactions."""
    rectangles: list[fitz.Rect] = []
    current_line: int | None = None
    current: fitz.Rect | None = None
    for position in positions[match.start():match.end()]:
        if position is None:
            if current is not None:
                rectangles.append(current)
                current = None
                current_line = None
            continue
        line, rect = position
        if current is None or line != current_line:
            if current is not None:
                rectangles.append(current)
            current_line, current = line, fitz.Rect(rect)
        else:
            current.include_rect(rect)
    if current is not None:
        rectangles.append(current)
    return rectangles


def _add_pattern_matches(
    page: fitz.Page, text: str, pattern: re.Pattern[str], positions: list[tuple[int, fitz.Rect] | None], color: tuple[float, float, float],
    options: RedactionOptions, only_first: bool = False,
) -> bool:
    """Add exact-span redactions and return whether at least one match was found."""
    found = False
    for match in pattern.finditer(text):
        if match.start() == match.end():
            continue
        for rect in _match_rectangles(match, positions):
            _add(page, rect, color, options)
        found = True
        if only_first:
            return True
    return found


def redact_pdf_document(
    document: Document, targets: list[RedactionTarget], options: RedactionOptions, *, data: bytes | None = None
) -> Document:
    """Apply all targets in one pass so permanent redactions are applied safely."""
    if data is None and document.base64data is None:
        raise ValueError("PDF engine requires in-memory document base64data")
    color = _color(options.fill_color)
    pdf = fitz.open(stream=data if data is not None else document.decoded_bytes(), filetype="pdf")
    try:
        for target in targets:
            if isinstance(target, BoundingBoxTarget):
                for box in target.values:
                    page = _page(pdf, box.page)
                    _add(page, _rect(page, box), color, options)
            elif isinstance(target, TextTarget):
                flags = re.IGNORECASE if target.ignore_case else 0
                pattern = re.compile("|".join(re.escape(value) for value in sorted(target.values, key=len, reverse=True)), flags)
                for page in _pages(pdf, target.pages):
                    text, positions = _text_index(page)
                    _add_pattern_matches(page, text, pattern, positions, color, options)
            elif isinstance(target, PageTarget):
                for page_number in target.values:
                    page = _page(pdf, page_number)
                    _add(page, page.rect, color, options)
            elif isinstance(target, RegexTarget):
                flags = re.IGNORECASE if target.ignore_case else 0
                if not target.allow_unicode:
                    flags |= re.ASCII
                patterns = [re.compile(pattern, flags) for pattern in target.patterns]
                matched = False
                for page in _pages(pdf, target.pages):
                    text, positions = _text_index(page)
                    for pattern in patterns:
                        if _add_pattern_matches(page, text, pattern, positions, color, options, target.only_first_match):
                            matched = True
                            if target.only_first_match:
                                break
                    if matched and target.only_first_match:
                        break
        if options.permanent_redaction:
            for page in pdf:
                page.apply_redactions()
        output = pdf.tobytes(garbage=4, deflate=True)
    finally:
        pdf.close()
    filename = Path(document.filename or "document.pdf")
    return Document(
        base64data=base64.b64encode(output).decode("ascii"),
        mime_type="application/pdf",
        filename=f"{filename.stem}-redacted.pdf",
    )
