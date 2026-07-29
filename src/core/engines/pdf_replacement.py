"""PyMuPDF implementation of permanent, layout-aware PDF text replacement."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import fitz

from src.core.models.document import Document
from src.core.engines.pdf_redaction import _match_rectangles, _text_index
from src.core.models.replacement import ReplacementTarget
from src.core.services.replacement_text import replacement_text


def _replacement_lines(original: str, replacement: str, static: bool) -> list[str]:
    """Keep line breaks aligned with a source phrase that spans PDF lines."""
    lines = original.splitlines()
    if len(lines) <= 1:
        return [replacement]
    if not static:
        return replacement.splitlines()
    # A static marker has no natural line breaks; split it in source-line proportions.
    weights = [max(1, len(line)) for line in lines]
    total = sum(weights)
    result: list[str] = []
    position = 0
    consumed_weight = 0
    for index, weight in enumerate(weights):
        consumed_weight += weight
        end = len(replacement) if index == len(weights) - 1 else round(len(replacement) * consumed_weight / total)
        result.append(replacement[position:end])
        position = end
    return result


def _insert(page: fitz.Page, rect: fitz.Rect, value: str) -> None:
    """Insert within the removed text area, reducing size before a visible fallback."""
    if not value:
        return
    # The original line height is a useful starting size and handles normal text inline.
    size = max(4.0, rect.height * 0.78)
    while size >= 4.0:
        if page.insert_textbox(rect, value, fontsize=size, fontname="helv", color=(0, 0, 0)) >= 0:
            return
        size -= 0.5
    # Long static values cannot always fit. This keeps them near the source context.
    page.insert_text((rect.x0, rect.y0 + max(4.0, rect.height * 0.75)), value, fontsize=4, fontname="helv", color=(0, 0, 0))


def replace_pdf_document(document: Document, targets: list[ReplacementTarget], *, data: bytes | None = None) -> Document:
    """Permanently remove source text, then insert replacement text at its location."""
    if data is None and document.base64data is None:
        raise ValueError("PDF engine requires in-memory document base64data")
    pdf = fitz.open(stream=data if data is not None else document.decoded_bytes(), filetype="pdf")
    insertions: list[tuple[int, fitz.Rect, str]] = []
    try:
        for target in targets:
            page_numbers = target.pages if target.pages is not None else range(pdf.page_count)
            for number in page_numbers:
                if not 0 <= number < pdf.page_count:
                    raise ValueError(f"Page {number} is out of range (document has {pdf.page_count} pages)")
                page = pdf[number]
                flags = re.IGNORECASE if target.ignore_case else 0
                pattern = re.compile("|".join(re.escape(value) for value in sorted(target.values, key=len, reverse=True)), flags)
                text, positions = _text_index(page)
                for match in pattern.finditer(text):
                    if match.start() == match.end():
                        continue
                    original = match.group()
                    replacement = replacement_text(original, target)
                    rects = _match_rectangles(match, positions)
                    values = _replacement_lines(original, replacement, target.replacement_type == "STATIC")
                    match_values = [replacement] if len(rects) == 1 else values
                    for rect, value in zip(rects, match_values):
                        page.add_redact_annot(rect, fill=False)
                        insertions.append((number, rect, value))
        # Applying annotations removes the original content from the PDF content stream.
        for page in pdf:
            page.apply_redactions()
        for page_number, rect, value in insertions:
            _insert(pdf[page_number], rect, value)
        output = pdf.tobytes(garbage=4, deflate=True)
    finally:
        pdf.close()
    filename = Path(document.filename or "document.pdf")
    return Document(
        base64data=base64.b64encode(output).decode("ascii"),
        mime_type="application/pdf",
        filename=f"{filename.stem}-replaced.pdf",
    )
