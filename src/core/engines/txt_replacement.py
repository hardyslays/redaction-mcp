"""UTF-8 plain-text replacement engine."""

from __future__ import annotations

import base64
from pathlib import Path

from src.core.models.document import Document
from src.core.models.replacement import ReplacementTarget
from src.core.services.replacement_text import replacement_text
from src.core.services.text_matching import compile_text_pattern


def replace_txt_document(
    document: Document, targets: list[ReplacementTarget], *, data: bytes | None = None
) -> Document:
    """Replace matching text on TXT's single logical page."""
    source = (data if data is not None else document.decoded_bytes()).decode("utf-8")
    for target in targets:
        if target.pages is not None and any(page != 0 for page in target.pages):
            raise ValueError("TXT data replacement supports only page index 0")
        pattern = compile_text_pattern(
            target.values, ignore_case=target.ignore_case, partial_match=target.partial_match
        )
        source = pattern.sub(lambda match: replacement_text(match.group(), target), source)

    filename = Path(document.filename or "document.txt")
    return Document(
        base64data=base64.b64encode(source.encode("utf-8")).decode("ascii"),
        mime_type="text/plain",
        filename=f"{filename.stem}-replaced.txt",
    )
