"""PyMuPDF implementation of permanent, layout-aware PDF text replacement."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import re

import fitz

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.engines.pdf_redaction import _match_rectangles
from redaction_mcp.core.models.replacement import ReplacementTarget
from redaction_mcp.core.services.replacement_text import replacement_text
from redaction_mcp.core.services.text_matching import compile_text_pattern


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


@dataclass(frozen=True)
class _TextStyle:
    """Rendering details for one source character (including its text baseline)."""

    size: float
    font: str
    flags: int
    color: tuple[float, float, float]
    baseline: fitz.Point
    background: tuple[float, float, float] | None = None


def _rgb(color: int) -> tuple[float, float, float]:
    return tuple(((color >> shift) & 255) / 255 for shift in (16, 8, 0))  # type: ignore[return-value]


def _background_color(page: fitz.Page, rect: fitz.Rect) -> tuple[float, float, float] | None:
    """Estimate the surrounding page colour from eight near-edge span samples."""
    samples: list[tuple[int, int, int]] = []
    inset = min(0.25, rect.width / 8, rect.height / 8)
    middle_x = (rect.x0 + rect.x1) / 2
    middle_y = (rect.y0 + rect.y1) / 2
    for point in (
        (rect.x0 + inset, rect.y0 + inset),
        (rect.x1 - inset, rect.y0 + inset),
        (rect.x0 + inset, rect.y1 - inset),
        (rect.x1 - inset, rect.y1 - inset),
        (middle_x, rect.y0 + inset),
        (middle_x, rect.y1 - inset),
        (rect.x0 + inset, middle_y),
        (rect.x1 - inset, middle_y),
    ):
        clip = fitz.Rect(point[0] - 0.25, point[1] - 0.25, point[0] + 0.25, point[1] + 0.25) & page.rect
        if clip.is_empty:
            continue
        pixmap = page.get_pixmap(clip=clip, alpha=False)
        if pixmap.samples:
            pixels = list(zip(*(iter(pixmap.samples),) * pixmap.n))
            samples.append(tuple(sum(pixel[index] for pixel in pixels) // len(pixels) for index in range(3)))
    if not samples:
        return None
    return tuple(
        sum(sample[index] for sample in samples) / (255 * len(samples)) for index in range(3)
    )  # type: ignore[return-value]


def _fontname(style: _TextStyle) -> str:
    """Map common PDF font-family names to the Base 14 fonts PyMuPDF can embed.

    PDF fonts are frequently subsetted and therefore cannot be reused by name.
    Mapping the family and bold/italic traits gives a reliable rendering fallback
    for common sans, serif, monospaced, Symbol, and Dingbats fonts.
    """
    source = style.font.lower().replace("-", " ")
    bold = bool(style.flags & 16) or any(token in source for token in ("bold", "black", "semibold", "demi"))
    italic = bool(style.flags & 2) or any(token in source for token in ("italic", "oblique", "slanted"))
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
        # Arial, Helvetica, Calibri, Verdana, Tahoma, and most embedded fonts are sans-serif.
        family = ("helv", "hebo", "heit", "hebi")
    return family[(2 if italic else 0) + (1 if bold else 0)]


def _text_index_with_styles(page: fitz.Page) -> tuple[str, list[tuple[int, fitz.Rect] | None], list[_TextStyle | None]]:
    """Return the redaction text index together with source span appearance data."""
    text: list[str] = []
    positions: list[tuple[int, fitz.Rect] | None] = []
    styles: list[_TextStyle | None] = []
    line_number = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                color = _rgb(span.get("color", 0))
                background = _background_color(page, fitz.Rect(span["bbox"]))
                for char in span.get("chars", []):
                    text.append(char["c"])
                    positions.append((line_number, fitz.Rect(char["bbox"])))
                    styles.append(
                        _TextStyle(
                            size=span["size"], font=span["font"], flags=span.get("flags", 0), color=color,
                            baseline=fitz.Point(char["origin"]), background=background,
                        )
                    )
            text.append("\n")
            positions.append(None)
            styles.append(None)
            line_number += 1
    return "".join(text), positions, styles


def _match_styles(
    match: re.Match[str], positions: list[tuple[int, fitz.Rect] | None], styles: list[_TextStyle | None]
) -> list[_TextStyle]:
    """Use the first character's span data for each matched visual line."""
    result: list[_TextStyle] = []
    current_line: int | None = None
    for position, style in zip(positions[match.start():match.end()], styles[match.start():match.end()]):
        if position is None:
            current_line = None
        elif position[0] != current_line:
            current_line = position[0]
            if style is not None:
                result.append(style)
    return result


def _insert(page: fitz.Page, rect: fitz.Rect, value: str, style: _TextStyle) -> None:
    """Insert on the original baseline using the source font traits and colour."""
    if not value:
        return
    fontname = _fontname(style)
    size = style.size
    try:
        width = fitz.get_text_length(value, fontname=fontname, fontsize=size)
        if width > rect.width:
            size = max(4.0, size * rect.width / width)
    except (RuntimeError, ValueError):
        fontname = "helv"
    # The matching redaction uses the sampled background colour, so the replacement
    # does not sit on PyMuPDF's default white redaction fill.
    # Using the source baseline prevents the usual top-aligned textbox appearance.
    page.insert_text(style.baseline, value, fontsize=size, fontname=fontname, color=style.color)


def replace_pdf_document(document: Document, targets: list[ReplacementTarget], *, data: bytes | None = None) -> Document:
    """Permanently remove source text, then insert replacement text at its location."""
    if data is None and document.base64data is None:
        raise ValueError("PDF engine requires in-memory document base64data")
    pdf = fitz.open(stream=data if data is not None else document.decoded_bytes(), filetype="pdf")
    insertions: list[tuple[int, fitz.Rect, str, _TextStyle]] = []
    try:
        for target in targets:
            page_numbers = target.pages if target.pages is not None else range(pdf.page_count)
            for number in page_numbers:
                if not 0 <= number < pdf.page_count:
                    raise ValueError(f"Page {number} is out of range (document has {pdf.page_count} pages)")
                page = pdf[number]
                pattern = compile_text_pattern(
                    target.values, ignore_case=target.ignore_case, partial_match=target.partial_match
                )
                text, positions, styles = _text_index_with_styles(page)
                for match in pattern.finditer(text):
                    if match.start() == match.end():
                        continue
                    original = match.group()
                    replacement = replacement_text(original, target)
                    rects = _match_rectangles(match, positions)
                    matched_styles = _match_styles(match, positions, styles)
                    values = _replacement_lines(original, replacement, target.replacement_type == "STATIC")
                    match_values = [replacement] if len(rects) == 1 else values
                    for rect, value, style in zip(rects, match_values, matched_styles):
                        page.add_redact_annot(rect, fill=style.background if style.background is not None else False)
                        insertions.append((number, rect, value, style))
        # Applying annotations removes the original content from the PDF content stream.
        for page in pdf:
            page.apply_redactions()
        for page_number, rect, value, style in insertions:
            _insert(pdf[page_number], rect, value, style)
        output = pdf.tobytes(garbage=4, deflate=True)
    finally:
        pdf.close()
    filename = Path(document.filename or "document.pdf")
    return Document(
        base64data=base64.b64encode(output).decode("ascii"),
        mime_type="application/pdf",
        filename=f"{filename.stem}-replaced.pdf",
    )
