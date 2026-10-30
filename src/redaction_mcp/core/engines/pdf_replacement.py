"""PyMuPDF implementation of permanent, layout-aware PDF text replacement."""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from math import atan2, degrees
from pathlib import Path
import re

import pymupdf as fitz

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.engines.pdf_redaction import (
    _CharacterPosition,
    _RenderableCharacter,
    _base14_fontname,
    _fill,
    _match_regions,
    _renderable_characters,
    _restoration_candidates,
    _restore_characters,
    _tight_char_quad,
)
from redaction_mcp.core.models.replacement import ReplacementTarget
from redaction_mcp.core.services.replacement_text import ReplacementContext, replacement_text
from redaction_mcp.core.services.text_matching import TextMatch, find_text_matches


def _replacement_lines(original: str, replacement: str, static: bool) -> list[str]:
    """Keep line breaks aligned with a source phrase that spans PDF lines."""
    lines = original.splitlines()
    if len(lines) <= 1:
        return [" ".join(replacement.splitlines())]
    replacement_lines = replacement.splitlines()
    if not static and len(replacement_lines) == len(lines):
        return replacement_lines
    if not static:
        replacement = " ".join(replacement_lines)
    # Split a single generated value in the source line proportions.
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
    direction: tuple[float, float] = (1.0, 0.0)
    background: tuple[float, float, float] | None = None
    span_bbox: fitz.Rect | None = None


def _rgb(color: int) -> tuple[float, float, float]:
    return tuple(((color >> shift) & 255) / 255 for shift in (16, 8, 0))  # type: ignore[return-value]


def _background_color(page: fitz.Page, rect: fitz.Rect) -> tuple[float, float, float]:
    """Estimate the surrounding colour using the dominant bucketed edge pixel."""
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
        pixmap = page.get_pixmap(clip=clip, alpha=False, annots=False)
        if pixmap.samples:
            pixels = list(zip(*(iter(pixmap.samples),) * pixmap.n))
            samples.extend(tuple(pixel[:3]) for pixel in pixels)
    if not samples:
        return (1.0, 1.0, 1.0)
    buckets = Counter(tuple(round(channel / 4) * 4 for channel in sample) for sample in samples)
    dominant = buckets.most_common(1)[0][0]
    return tuple(min(channel, 255) / 255 for channel in dominant)  # type: ignore[return-value]


def _fontname(style: _TextStyle) -> str:
    """Map common PDF font-family names to the Base 14 fonts PyMuPDF can embed.

    PDF fonts are frequently subsetted and therefore cannot be reused by name.
    Mapping the family and bold/italic traits gives a reliable rendering fallback
    for common sans, serif, monospaced, Symbol, and Dingbats fonts.
    """
    return _base14_fontname(style.font, style.flags)


def _text_index_with_styles(page: fitz.Page) -> tuple[str, list[_CharacterPosition | None], list[_TextStyle | None]]:
    """Return the redaction text index together with source span appearance data."""
    text: list[str] = []
    positions: list[_CharacterPosition | None] = []
    styles: list[_TextStyle | None] = []
    line_number = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span_number, span in enumerate(line["spans"]):
                color = _rgb(span.get("color", 0))
                span_bbox = fitz.Rect(span["bbox"])
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
                    styles.append(
                        _TextStyle(
                            size=span["size"], font=span["font"], flags=span.get("flags", 0), color=color,
                            baseline=fitz.Point(char["origin"]), direction=tuple(line["dir"]), span_bbox=span_bbox,
                        )
                    )
            text.append("\n")
            positions.append(None)
            styles.append(None)
            line_number += 1
    return "".join(text), positions, styles


def _match_styles(
    match: re.Match[str] | TextMatch, positions: list[_CharacterPosition | None], styles: list[_TextStyle | None]
) -> list[_TextStyle]:
    """Use the first character's style for each matched source span."""
    result: list[_TextStyle] = []
    current_key: tuple[int, int] | None = None
    for position, style in zip(positions[match.start():match.end()], styles[match.start():match.end()]):
        if position is None:
            current_key = None
        elif (position.line, position.span) != current_key:
            current_key = (position.line, position.span)
            if style is not None:
                result.append(style)
    return result


def _insert(page: fitz.Page, quad: fitz.Quad, value: str, style: _TextStyle) -> None:
    """Insert on the original baseline using the source font traits and colour."""
    if not value:
        return
    fontname = _fontname(style)
    size = style.size
    try:
        width = fitz.get_text_length(value, fontname=fontname, fontsize=size)
        available_width = max(0.0, quad.width)
        if width > available_width:
            size = max(style.size * 0.8, size * available_width / width)
    except (RuntimeError, ValueError):
        fontname = "helv"
    # The matching redaction uses the sampled background colour, so the replacement
    # does not sit on PyMuPDF's default white redaction fill.
    # Using the source baseline prevents the usual top-aligned textbox appearance.
    angle = degrees(atan2(-style.direction[1], style.direction[0])) % 360
    morph = None if min(angle, 360 - angle) < 0.01 else (style.baseline, fitz.Matrix(angle))
    page.insert_text(
        style.baseline,
        value,
        fontsize=size,
        fontname=fontname,
        color=style.color,
        morph=morph,
    )


def replace_pdf_document(
    document: Document,
    targets: list[ReplacementTarget],
    *,
    data: bytes | None = None,
    source_path: Path | None = None,
    output_path: Path | None = None,
) -> Document:
    """Permanently remove source text, then insert replacement text at its location."""
    if data is None and document.base64data is None:
        raise ValueError("PDF engine requires in-memory document base64data")
    pdf = fitz.open(source_path) if source_path is not None else fitz.open(
        stream=data if data is not None else document.decoded_bytes(), filetype="pdf"
    )
    context = ReplacementContext()
    try:
        for target in targets:
            for number in target.pages or []:
                if not 0 <= number < pdf.page_count:
                    raise ValueError(f"Page {number} is out of range (document has {pdf.page_count} pages)")
        for number in range(pdf.page_count):
            selected = [target for target in targets if target.pages is None or number in target.pages]
            if not selected:
                continue
            page = pdf[number]
            text, positions, styles = _text_index_with_styles(page)
            characters: list[_RenderableCharacter] | None = None
            redactions: list[tuple[fitz.Quad, tuple[float, float, float]]] = []
            restorations: list[_RenderableCharacter] = []
            target_indices: set[int] = set()
            insertions: list[tuple[fitz.Quad, str, _TextStyle]] = []
            backgrounds: dict[tuple[float, float, float, float], tuple[float, float, float]] = {}
            for target in selected:
                for match in find_text_matches(
                    text,
                    target.values,
                    ignore_case=target.ignore_case,
                    partial_match=target.partial_match,
                ):
                    original = match.group()
                    replacement = context.replacement_text(original, target)
                    regions = _match_regions(match, positions)
                    matched_styles = _match_styles(match, positions, styles)
                    line_values = _replacement_lines(original, replacement, target.replacement_type == "STATIC")
                    line_groups: list[list[int]] = []
                    last_line: int | None = None
                    for index, region in enumerate(regions):
                        position = positions[min(region.text_indices)]
                        line = position.line if position is not None else -1
                        if line != last_line:
                            line_groups.append([])
                            last_line = line
                        line_groups[-1].append(index)
                    for line_index, group in enumerate(line_groups):
                        if line_index >= len(line_values) or group[0] >= len(matched_styles):
                            continue
                        first_quad = regions[group[0]].text_quad
                        last_quad = regions[group[-1]].text_quad
                        whole_line = fitz.Quad(first_quad.ul, last_quad.ur, first_quad.ll, last_quad.lr)
                        insertions.append((whole_line, line_values[line_index], matched_styles[group[0]]))
                    for index, region in enumerate(regions):
                        style = matched_styles[index] if index < len(matched_styles) else None
                        if characters is None:
                            characters = _renderable_characters(page)
                        target_indices.update(region.text_indices)
                        restorations.extend(
                            _restoration_candidates(characters, region.quad, region.text_indices)
                        )
                        sample_rect = style.span_bbox if style is not None and style.span_bbox is not None else region.quad.rect
                        background_key = tuple(sample_rect)
                        if background_key not in backgrounds:
                            backgrounds[background_key] = _background_color(page, sample_rect)
                        background = backgrounds[background_key]
                        redactions.append((region.quad, background))
                        page.add_redact_annot(region.quad, fill=None, cross_out=False)
            if redactions:
                # Applying annotations removes the source from the content stream.
                page.apply_redactions()
                for quad, background in redactions:
                    _fill(page, quad, background, 1.0)
                _restore_characters(page, restorations, target_indices)
                for quad, value, style in insertions:
                    _insert(page, quad, value, style)
            del text, positions, styles, characters
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
        filename=f"{filename.stem}-replaced.pdf",
    )
