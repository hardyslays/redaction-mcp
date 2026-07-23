import base64
from io import BytesIO

import fitz
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.util import Inches

from src.core.models.document import Document
from src.core.models.errors import RedactionError
from src.core.models.redaction import BoundingBox, BoundingBoxTarget, PageTarget, RedactionOptions, RegexTarget, TextTarget
from src.core.services.redaction_service import redact_document


def pdf_with_text(text: str = "secret visible") -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    data = pdf.tobytes()
    pdf.close()
    return data


def docx_with_text(text: str = "secret visible") -> bytes:
    document = DocxDocument()
    document.add_paragraph(text)
    data = BytesIO()
    document.save(data)
    return data.getvalue()


def pptx_with_text(text: str = "secret visible") -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    shape.text = text
    data = BytesIO()
    presentation.save(data)
    return data.getvalue()


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
    assert result.message == "Only PDF, DOCX, and PPTX documents are currently supported"


def test_docx_text_redaction_removes_matching_text() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(docx_with_text()).decode(), filename="source.docx"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    assert result.filename == "source-redacted.docx"
    output = DocxDocument(BytesIO(base64.b64decode(result.base64data)))
    assert "secret" not in output.paragraphs[0].text
    assert "******" in output.paragraphs[0].text
    assert "visible" in output.paragraphs[0].text


def test_docx_page_redaction_returns_typed_error() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(docx_with_text()).decode(), filename="source.docx"),
        [PageTarget(type="page", values=[0])],
    )

    assert isinstance(result, RedactionError)
    assert result.message == "DOCX redaction supports only text and regex targets"


def test_docx_regex_redaction_removes_matching_text() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(docx_with_text("ID AB123456 visible")).decode(), filename="source.docx"),
        [RegexTarget(type="regex", patterns=[r"[A-Z]{2}\d{6}"], ignore_case=False)],
    )

    assert isinstance(result, Document)
    output = DocxDocument(BytesIO(base64.b64decode(result.base64data)))
    assert "AB123456" not in output.paragraphs[0].text
    assert "visible" in output.paragraphs[0].text


def test_pptx_text_redaction_removes_matching_text() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text()).decode(), filename="source.pptx"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    assert result.filename == "source-redacted.pptx"
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert "secret" not in output.slides[0].shapes[0].text
    assert "******" in output.slides[0].shapes[0].text
    assert "visible" in output.slides[0].shapes[0].text


def test_mask_redaction_replaces_text_with_redact_marker() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(docx_with_text()).decode(), filename="source.docx"),
        [TextTarget(type="text", values=["secret"])],
        RedactionOptions(redaction_type="mask"),
    )

    assert isinstance(result, Document)
    output = DocxDocument(BytesIO(base64.b64decode(result.base64data)))
    assert "secret" not in output.paragraphs[0].text
    assert "[REDACT]" in output.paragraphs[0].text


def test_pptx_bounding_box_adds_redaction_cover() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text()).decode(), filename="source.pptx"),
        [BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0, y=0, width=1, height=1)])],
    )

    assert isinstance(result, Document)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert len(output.slides[0].shapes) == 2


def test_pptx_page_redaction_adds_full_slide_cover() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text()).decode(), filename="source.pptx"),
        [PageTarget(type="page", values=[0])],
    )

    assert isinstance(result, Document)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    cover = output.slides[0].shapes[1]
    assert cover.left == 0
    assert cover.top == 0
    assert cover.width == output.slide_width
    assert cover.height == output.slide_height
