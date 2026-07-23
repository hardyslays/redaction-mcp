"""python-pptx implementation of PPTX redaction."""

from __future__ import annotations

import base64
import re
from io import BytesIO
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE

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


def _text_frames(shapes: object) -> list[object]:
    frames = []
    for shape in shapes:  # type: ignore[union-attr]
        if shape.has_text_frame:
            frames.append(shape.text_frame)
        if shape.has_table:
            frames.extend(cell.text_frame for row in shape.table.rows for cell in row.cells)
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            frames.extend(_text_frames(shape.shapes))
    return frames


def _replacement(text: str, options: RedactionOptions) -> str:
    return "[REDACT]" if options.redaction_type == "mask" else "*" * len(text)


def _replace(frame: object, pattern: re.Pattern[str], options: RedactionOptions, limit: int = 0) -> int:
    text, replacements = pattern.subn(lambda match: _replacement(match.group(), options), frame.text, count=limit)
    if replacements:
        frame.text = text
    return replacements


def _slides(presentation: Presentation, selection: list[int] | None) -> list[object]:
    indexes = selection if selection is not None else range(len(presentation.slides))
    slides = []
    for index in indexes:
        if not 0 <= index < len(presentation.slides):
            raise ValueError(f"Slide {index} is out of range (presentation has {len(presentation.slides)} slides)")
        slides.append(presentation.slides[index])
    return slides


def _color(hex_color: str) -> RGBColor:
    value = hex_color.lstrip("#")
    if len(value) != 6 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError("fill_color must be a six-digit hex colour, e.g. #000000")
    return RGBColor.from_string(value)


def _add_cover(slide: object, x: int, y: int, width: int, height: int, color: RGBColor) -> None:
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def _box(box: BoundingBox, presentation: Presentation) -> tuple[int, int, int, int]:
    if box.units == "normalized":
        return (
            int(box.x * presentation.slide_width), int(box.y * presentation.slide_height),
            int(box.width * presentation.slide_width), int(box.height * presentation.slide_height),
        )
    scale = 914400 if box.units == "inches" else 12700
    return int(box.x * scale), int(box.y * scale), int(box.width * scale), int(box.height * scale)


def redact_pptx_document(document: Document, targets: list[RedactionTarget], options: RedactionOptions) -> Document:
    """Apply text redactions and visual covers to a PPTX package."""
    if document.base64data is None:
        raise ValueError("PPTX engine requires in-memory document base64data")
    presentation = Presentation(BytesIO(document.decoded_bytes()))
    color = _color(options.fill_color)
    for target in targets:
        if isinstance(target, TextTarget):
            for slide in _slides(presentation, target.pages):
                for value in target.values:
                    pattern = re.compile(re.escape(value))
                    for frame in _text_frames(slide.shapes):
                        _replace(frame, pattern, options)
        elif isinstance(target, RegexTarget):
            flags = re.IGNORECASE if target.ignore_case else 0
            if not target.allow_unicode:
                flags |= re.ASCII
            for slide in _slides(presentation, target.pages):
                frames = _text_frames(slide.shapes)
                matched = False
                for pattern in (re.compile(value, flags) for value in target.patterns):
                    for frame in frames:
                        replaced = _replace(frame, pattern, options, limit=1 if target.only_first_match else 0)
                        if replaced and target.only_first_match:
                            matched = True
                            break
                    if matched:
                        break
        elif isinstance(target, BoundingBoxTarget):
            for box in target.values:
                slide = _slides(presentation, [box.page])[0]
                _add_cover(slide, *_box(box, presentation), color)
        elif isinstance(target, PageTarget):
            for slide in _slides(presentation, target.values):
                _add_cover(slide, 0, 0, presentation.slide_width, presentation.slide_height, color)
    output = BytesIO()
    presentation.save(output)
    filename = Path(document.filename or "document.pptx")
    return Document(
        base64data=base64.b64encode(output.getvalue()).decode("ascii"),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{filename.stem}-redacted.pptx",
    )
