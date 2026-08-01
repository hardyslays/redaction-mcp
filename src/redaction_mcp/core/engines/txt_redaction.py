"""UTF-8 plain-text redaction engine."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.redaction import (
    BoundingBoxTarget,
    PageTarget,
    RedactionOptions,
    RedactionTarget,
    RegexTarget,
    TextTarget,
)
from redaction_mcp.core.services.text_matching import compile_text_pattern


def _validate_pages(pages: list[int] | None) -> None:
    if pages is not None and any(page != 0 for page in pages):
        raise ValueError("TXT redaction supports only page index 0")


def _asterisks(text: str) -> str:
    return "*" * len(text)


def _page_asterisks(text: str) -> str:
    return "".join(char if char.isspace() else "*" for char in text)


def redact_txt_document(
    document: Document, targets: list[RedactionTarget], options: RedactionOptions, *, data: bytes | None = None
) -> Document:
    """Redact UTF-8 text while keeping the original line and whitespace layout."""
    del options
    source = (data if data is not None else document.decoded_bytes()).decode("utf-8")
    for target in targets:
        if isinstance(target, BoundingBoxTarget):
            raise ValueError("TXT redaction does not support bounding-box targets")
        if isinstance(target, PageTarget):
            _validate_pages(target.values)
            source = _page_asterisks(source)
        elif isinstance(target, TextTarget):
            _validate_pages(target.pages)
            pattern = compile_text_pattern(
                target.values, ignore_case=target.ignore_case, partial_match=target.partial_match
            )
            source = pattern.sub(lambda match: _asterisks(match.group()), source)
        elif isinstance(target, RegexTarget):
            _validate_pages(target.pages)
            flags = re.IGNORECASE if target.ignore_case else 0
            if not target.allow_unicode:
                flags |= re.ASCII
            for value in target.patterns:
                pattern = re.compile(value, flags)
                source, replacements = pattern.subn(
                    lambda match: _asterisks(match.group()), source, count=1 if target.only_first_match else 0
                )
                if replacements and target.only_first_match:
                    break

    filename = Path(document.filename or "document.txt")
    return Document(
        base64data=base64.b64encode(source.encode("utf-8")).decode("ascii"),
        mime_type="text/plain",
        filename=f"{filename.stem}-redacted.txt",
    )
