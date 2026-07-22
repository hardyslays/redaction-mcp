"""Transport-independent document redaction service."""

import base64
from urllib.request import urlopen

from src.core.engines.pdf_redaction import redact_pdf_document
from src.core.models.document import Document, SourceType
from src.core.models.errors import RedactionError
from src.core.models.redaction import RedactionOptions, RedactionTarget
from src.core.services.mime_detection import detect_mime_type


def _document_bytes(document: Document) -> bytes:
    source = document.get_source()
    if source.type is SourceType.BASE64DATA:
        return document.decoded_bytes()
    if source.type is SourceType.PATH:
        with open(source.value, "rb") as file:  # type: ignore[arg-type]
            return file.read()
    with urlopen(source.value, timeout=30) as response:  # nosec B310 - caller supplied document URL
        return response.read()


def redact_document(
    document: Document, targets: list[RedactionTarget], options: RedactionOptions | None = None
) -> Document | RedactionError:
    """Redact a document and return a new in-memory :class:`Document`.

    The core deliberately returns a typed error rather than leaking transport- or
    engine-specific exceptions to FastAPI and MCP callers.
    """
    options = options or RedactionOptions()
    try:
        data = _document_bytes(document)
        mime_type = document.mime_type or detect_mime_type(data, document.filename)
        if mime_type != "application/pdf":
            return RedactionError(message="Only PDF documents are currently supported")
        normalized = document.model_copy(update={
            "base64data": base64.b64encode(data).decode("ascii"),
            "path": None,
            "url": None,
            "mime_type": mime_type,
        })
        return redact_pdf_document(normalized, targets, options)
    except Exception as exc:
        return RedactionError(message=str(exc))
