"""Small, dependency-free MIME type helpers."""

from pathlib import Path


PDF_MIME_TYPE = "application/pdf"


def detect_mime_type(data: bytes | None = None, filename: str | None = None) -> str | None:
    """Infer the supported type from the PDF signature or filename."""
    if data and data.lstrip().startswith(b"%PDF-"):
        return PDF_MIME_TYPE
    if filename and Path(filename).suffix.lower() == ".pdf":
        return PDF_MIME_TYPE
    return None
