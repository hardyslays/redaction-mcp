"""Transport-independent document redaction service."""

from urllib.request import urlopen

from src.core.engines.docx_redaction import redact_docx_document
from src.core.engines.pdf_redaction import redact_pdf_document
from src.core.engines.pptx_redaction import redact_pptx_document
from src.core.models.document import Document, SourceType
from src.core.models.errors import RedactionError
from src.core.models.redaction import RedactionOptions, RedactionTarget
from src.core.services.mime_detection import DOCX_MIME_TYPE, PDF_MIME_TYPE, PPTX_MIME_TYPE, detect_mime_type


def _document_bytes(document: Document) -> bytes:
    source = document.get_source()
    if source.type is SourceType.BASE64DATA:
        return document.decoded_bytes()
    if source.type is SourceType.PATH:
        with open(source.value, "rb") as file:  # type: ignore[arg-type]
            return file.read()
    with urlopen(source.value, timeout=30) as response:  # if caller supplied document URL
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
        if mime_type not in (PDF_MIME_TYPE, DOCX_MIME_TYPE, PPTX_MIME_TYPE):
            return RedactionError(message="Only PDF, DOCX, and PPTX documents are currently supported")

        if mime_type == PDF_MIME_TYPE:
            return redact_pdf_document(document, targets, options, data=data)
        if mime_type == DOCX_MIME_TYPE:
            return redact_docx_document(document, targets, options, data=data)
        return redact_pptx_document(document, targets, options, data=data)
    except Exception as exc:
        return RedactionError(message=str(exc))
