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
    PolygonTarget,
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


def redact_pdf_document(document: Document, targets: list[RedactionTarget], options: RedactionOptions) -> Document:
    """Apply all targets in one pass so permanent redactions are applied safely."""
    if document.base64data is None:
        raise ValueError("PDF engine requires in-memory document base64data")
    color = _color(options.fill_color)
    pdf = fitz.open(stream=document.decoded_bytes(), filetype="pdf")
    try:
        for target in targets:
            if isinstance(target, BoundingBoxTarget):
                for box in target.values:
                    page = _page(pdf, box.page)
                    _add(page, _rect(page, box), color, options)
            elif isinstance(target, TextTarget):
                for page in _pages(pdf, target.pages):
                    for text in target.values:
                        for rect in page.search_for(text):
                            _add(page, rect, color, options)
            elif isinstance(target, PolygonTarget):
                for polygon in target.values:
                    page = _page(pdf, polygon.page)
                    xs, ys = [point.x for point in polygon.points], [point.y for point in polygon.points]
                    _add(page, fitz.Rect(min(xs), min(ys), max(xs), max(ys)), color, options)
            elif isinstance(target, PageTarget):
                for page_number in target.values:
                    page = _page(pdf, page_number)
                    _add(page, page.rect, color, options)
            elif isinstance(target, RegexTarget):
                flags = re.IGNORECASE if target.ignore_case else 0
                if not target.allow_unicode:
                    flags |= re.ASCII
                patterns = [re.compile(pattern, flags) for pattern in target.patterns]
                for page in _pages(pdf, target.pages):
                    matches = 0
                    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
                        if any(pattern.search(word) for pattern in patterns):
                            _add(page, fitz.Rect(x0, y0, x1, y1), color, options)
                            matches += 1
                            if target.only_first_match and matches == 1:
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
