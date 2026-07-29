"""Small MIME type helpers."""

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile


PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PPTM_MIME_TYPE = "application/vnd.ms-powerpoint.presentation.macroEnabled.12"
PPTM_PACKAGE_MIME_TYPE = "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"
TEXT_MIME_TYPE = "text/plain"


def _office_mime_type(data: bytes) -> str | None:
    try:
        with ZipFile(BytesIO(data)) as archive:
            content_types = archive.read("[Content_Types].xml")
    except (BadZipFile, KeyError):
        return None
    if b"wordprocessingml.document.main+xml" in content_types:
        return DOCX_MIME_TYPE
    if b"presentationml.presentation.main+xml" in content_types:
        return PPTX_MIME_TYPE
    if PPTM_PACKAGE_MIME_TYPE.encode() in content_types:
        return PPTM_MIME_TYPE
    return None


def detect_mime_type(data: bytes | None = None, filename: str | None = None) -> str | None:
    """Infer a supported type from a file signature, package metadata, or name."""
    if data and data.lstrip().startswith(b"%PDF-"):
        return PDF_MIME_TYPE
    if data:
        office_mime_type = _office_mime_type(data)
        if office_mime_type:
            return office_mime_type
    if filename:
        return {
            ".pdf": PDF_MIME_TYPE,
            ".docx": DOCX_MIME_TYPE,
            ".pptx": PPTX_MIME_TYPE,
            ".pptm": PPTM_MIME_TYPE,
            ".txt": TEXT_MIME_TYPE,
        }.get(Path(filename).suffix.lower())
    return None
