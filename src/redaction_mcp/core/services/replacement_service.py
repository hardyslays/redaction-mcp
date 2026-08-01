"""Transport-independent document data replacement service."""

from redaction_mcp.core.engines.docx_replacement import replace_docx_document
from redaction_mcp.core.engines.pdf_replacement import replace_pdf_document
from redaction_mcp.core.engines.pptx_replacement import replace_pptx_document
from redaction_mcp.core.engines.txt_replacement import replace_txt_document
from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.errors import ReplacementError
from redaction_mcp.core.models.replacement import ReplacementTarget
from redaction_mcp.core.services.mime_detection import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    PPTM_MIME_TYPE,
    PPTX_MIME_TYPE,
    TEXT_MIME_TYPE,
    detect_mime_type,
)

def replace_document(document: Document, targets: list[ReplacementTarget]) -> Document | ReplacementError:
    """Replace text in PDF, DOCX, PPTX, or TXT documents."""
    try:
        data = document.decoded_bytes()
        mime_type = document.mime_type or detect_mime_type(data, document.filename)
        if mime_type not in (PDF_MIME_TYPE, DOCX_MIME_TYPE, PPTX_MIME_TYPE, PPTM_MIME_TYPE, TEXT_MIME_TYPE):
            return ReplacementError(message="Only PDF, DOCX, PPTX, PPTM, and TXT documents are currently supported for data replacement")
        if mime_type == PDF_MIME_TYPE:
            return replace_pdf_document(document, targets, data=data)
        if mime_type == DOCX_MIME_TYPE:
            return replace_docx_document(document, targets, data=data)
        if mime_type == TEXT_MIME_TYPE:
            return replace_txt_document(document, targets, data=data)
        return replace_pptx_document(document, targets, data=data)
    except Exception as exc:
        return ReplacementError(message=str(exc))
