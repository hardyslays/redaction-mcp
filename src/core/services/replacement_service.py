"""Transport-independent document data replacement service."""

from urllib.request import urlopen

from src.core.engines.docx_replacement import replace_docx_document
from src.core.engines.pdf_replacement import replace_pdf_document
from src.core.engines.pptx_replacement import replace_pptx_document
from src.core.engines.txt_replacement import replace_txt_document
from src.core.models.document import Document, SourceType
from src.core.models.errors import ReplacementError
from src.core.models.replacement import ReplacementTarget
from src.core.services.mime_detection import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    PPTM_MIME_TYPE,
    PPTX_MIME_TYPE,
    TEXT_MIME_TYPE,
    detect_mime_type,
)


def _document_bytes(document: Document) -> bytes:
    source = document.get_source()
    if source.type is SourceType.BASE64DATA:
        return document.decoded_bytes()
    if source.type is SourceType.PATH:
        with open(source.value, "rb") as file:  # type: ignore[arg-type]
            return file.read()
    with urlopen(source.value, timeout=30) as response:  # pragma: no cover - network source
        return response.read()


def replace_document(document: Document, targets: list[ReplacementTarget]) -> Document | ReplacementError:
    """Replace text in PDF, DOCX, PPTX, or TXT documents."""
    try:
        data = _document_bytes(document)
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
