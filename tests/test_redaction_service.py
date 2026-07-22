import base64

import fitz

from src.core.models.document import Document
from src.core.models.errors import RedactionError
from src.core.models.redaction import BoundingBox, BoundingBoxTarget, TextTarget
from src.core.services.redaction_service import redact_document


def pdf_with_text(text: str = "secret visible") -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    data = pdf.tobytes()
    pdf.close()
    return data


def test_text_redaction_removes_matching_text() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pdf_with_text()).decode(), filename="source.pdf"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    assert result.filename == "source-redacted.pdf"
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    assert "visible" in output[0].get_text()
    output.close()


def test_normalized_bounding_box_redacts_area() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pdf_with_text("hide")).decode(), filename="source.pdf"),
        [BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0, y=0, width=1, height=1)])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert output[0].get_text().strip() == ""
    output.close()


def test_unsupported_document_returns_typed_error() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(b"plain text").decode(), filename="note.txt"),
        [TextTarget(type="text", values=["text"])],
    )

    assert isinstance(result, RedactionError)
    assert result.message == "Only PDF documents are currently supported"
