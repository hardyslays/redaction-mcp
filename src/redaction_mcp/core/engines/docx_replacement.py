"""python-docx implementation of permanent text data replacement."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument

from redaction_mcp.core.engines.docx_redaction import _paragraphs
from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.replacement import ReplacementTarget
from redaction_mcp.core.services.replacement_text import ReplacementContext
from redaction_mcp.core.services.text_matching import replace_text_matches


def _replace(paragraph: object, target: ReplacementTarget, context: ReplacementContext) -> None:
    text, count = replace_text_matches(
        paragraph.text,
        target.values,
        lambda match: context.replacement_text(match.group(), target),
        ignore_case=target.ignore_case,
        partial_match=target.partial_match,
    )
    if count:
        # Rebuilding the paragraph removes the original runs from the DOCX package.
        paragraph.clear()
        paragraph.add_run(text)


def replace_docx_document(document: Document, targets: list[ReplacementTarget], *, data: bytes | None = None) -> Document:
    """Replace exact text in body, table, header, and footer paragraphs."""
    if data is None and document.base64data is None:
        raise ValueError("DOCX engine requires in-memory document base64data")
    source = DocxDocument(BytesIO(data if data is not None else document.decoded_bytes()))
    paragraphs = _paragraphs(source)
    context = ReplacementContext()
    for target in targets:
        if target.pages is not None:
            raise ValueError("DOCX data replacement does not support page-restricted targets")
        for paragraph in paragraphs:
            _replace(paragraph, target, context)
    output = BytesIO()
    source.save(output)
    filename = Path(document.filename or "document.docx")
    return Document(
        base64data=base64.b64encode(output.getvalue()).decode("ascii"),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{filename.stem}-replaced.docx",
    )
