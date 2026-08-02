"""Transport-independent document redaction service."""

from redaction_mcp.core.engines.docx_redaction import redact_docx_document
from redaction_mcp.core.engines.pdf_redaction import redact_pdf_document
from redaction_mcp.core.engines.pptx_redaction import redact_pptx_document
from redaction_mcp.core.engines.txt_redaction import redact_txt_document
from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.errors import RedactionError
from redaction_mcp.core.models.redaction import RedactionOptions, RedactionTarget
from redaction_mcp.core.services.mime_detection import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    PPTM_MIME_TYPE,
    PPTX_MIME_TYPE,
    TEXT_MIME_TYPE,
    detect_mime_type,
)

def redact_document(
    document: Document, targets: list[RedactionTarget], options: RedactionOptions | None = None
) -> Document | RedactionError:
    """Redact a document and return a new in-memory :class:`Document`.

    The core deliberately returns a typed error rather than leaking transport- or
    engine-specific exceptions to FastAPI and MCP callers.
    """
    options = options or RedactionOptions()
    try:
        data = document.decoded_bytes()
        mime_type = document.mime_type or detect_mime_type(data, document.filename)
        if mime_type not in (PDF_MIME_TYPE, DOCX_MIME_TYPE, PPTX_MIME_TYPE, PPTM_MIME_TYPE, TEXT_MIME_TYPE):
            return RedactionError(message="Only PDF, DOCX, PPTX, PPTM, and TXT documents are currently supported")

        if mime_type == PDF_MIME_TYPE:
            return redact_pdf_document(document, targets, options, data=data)
        if mime_type == DOCX_MIME_TYPE:
            return redact_docx_document(document, targets, options, data=data)
        if mime_type == TEXT_MIME_TYPE:
            return redact_txt_document(document, targets, options, data=data)
        return redact_pptx_document(document, targets, options, data=data)
    except Exception as exc:
        return RedactionError(message=str(exc))
