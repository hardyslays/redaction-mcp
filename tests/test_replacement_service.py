import base64
from io import BytesIO

import fitz
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.util import Inches

from src.core.engines.pdf_replacement import _replacement_lines, replacement_text
from src.core.models.document import Document
from src.core.models.errors import ReplacementError
from src.core.models.replacement import TextReplacementTarget
from src.core.services.replacement_service import replace_document


def pdf_with_text(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    data = pdf.tobytes()
    pdf.close()
    return data


def target(kind: str, source: str = "Jane Doe", **values: object) -> TextReplacementTarget:
    return TextReplacementTarget(type="text", values=[source], replacement_type=kind, **values)


def docx_with_text(text: str) -> bytes:
    document = DocxDocument()
    document.add_paragraph(text)
    data = BytesIO()
    document.save(data)
    return data.getvalue()


def pptx_with_text(text: str) -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    shape.text = text
    data = BytesIO()
    presentation.save(data)
    return data.getvalue()


def test_partial_replacement_masks_non_whitespace_and_keeps_suffix() -> None:
    assert replacement_text("Jane Doe", target("PARTIAL")) == "**** *oe"
    assert replacement_text("AB CD", target("PARTIAL")) == "** CD"
    assert replacement_text("Jane\nDoe", target("PARTIAL")) == "****\n*oe"


def test_static_replacement_is_bracketed() -> None:
    assert replacement_text("Jane Doe", target("STATIC", static_text="REDACTED")) == "[REDACTED]"
    assert _replacement_lines("Long\nline", "[SAFE]", static=True) == ["[SA", "FE]"]


def test_regex_replacement_preserves_character_pattern_without_source_text() -> None:
    assert replacement_text("AB-129 z", target("REGEX")) == "BC-230 a"


def test_pdf_replacement_permanently_removes_original_and_inserts_static_value() -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(pdf_with_text("Owner Jane Doe")).decode(), filename="source.pdf"),
        [target("STATIC", static_text="REDACTED")],
    )

    assert not isinstance(result, ReplacementError)
    assert result.filename == "source-replaced.pdf"
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    text = output[0].get_text()
    assert "Jane Doe" not in text
    assert "[REDACTED]" in text
    output.close()


def test_docx_replacement_permanently_removes_original_for_all_strategies() -> None:
    for kind, expected, kwargs in (
        ("PARTIAL", "**** *oe", {}),
        ("STATIC", "[SAFE]", {"static_text": "SAFE"}),
        ("REGEX", "Kbof Epf", {}),
    ):
        result = replace_document(
            Document(base64data=base64.b64encode(docx_with_text("Owner Jane Doe")).decode(), filename="source.docx"),
            [target(kind, **kwargs)],
        )

        assert not isinstance(result, ReplacementError)
        assert result.filename == "source-replaced.docx"
        output = DocxDocument(BytesIO(base64.b64decode(result.base64data)))
        assert "Jane Doe" not in output.paragraphs[0].text
        assert expected in output.paragraphs[0].text


def test_docx_replacement_handles_multiline_text_and_rejects_page_filter() -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(docx_with_text("Jane\nDoe")).decode(), filename="source.docx"),
        [target("PARTIAL", source="Jane\nDoe")],
    )

    assert not isinstance(result, ReplacementError)
    output = DocxDocument(BytesIO(base64.b64decode(result.base64data)))
    assert output.paragraphs[0].text == "****\n*oe"

    filtered = replace_document(
        Document(base64data=base64.b64encode(docx_with_text("Jane Doe")).decode(), filename="source.docx"),
        [TextReplacementTarget(type="text", values=["Jane Doe"], replacement_type="PARTIAL", pages=[0])],
    )
    assert isinstance(filtered, ReplacementError)
    assert filtered.message == "DOCX data replacement does not support page-restricted targets"


def test_pptx_replacement_permanently_removes_original_for_all_strategies() -> None:
    for kind, expected, kwargs in (
        ("PARTIAL", "**** *oe", {}),
        ("STATIC", "[SAFE]", {"static_text": "SAFE"}),
        ("REGEX", "Kbof Epf", {}),
    ):
        result = replace_document(
            Document(base64data=base64.b64encode(pptx_with_text("Owner Jane Doe")).decode(), filename="source.pptx"),
            [target(kind, **kwargs)],
        )

        assert not isinstance(result, ReplacementError)
        assert result.filename == "source-replaced.pptx"
        output = Presentation(BytesIO(base64.b64decode(result.base64data)))
        text = output.slides[0].shapes[0].text
        assert "Jane Doe" not in text
        assert expected in text


def test_pptx_replacement_handles_multiline_text_and_slide_filter() -> None:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    first.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = "Jane\nDoe"
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    second.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = "Jane Doe"
    data = BytesIO()
    presentation.save(data)

    result = replace_document(
        Document(base64data=base64.b64encode(data.getvalue()).decode(), filename="source.pptx"),
        [TextReplacementTarget(type="text", values=["Jane\nDoe"], replacement_type="PARTIAL", pages=[0])],
    )

    assert not isinstance(result, ReplacementError)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert output.slides[0].shapes[0].text == "****\n*oe"
    assert output.slides[1].shapes[0].text == "Jane Doe"


def test_replacement_rejects_unsupported_document() -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(b"text").decode(), filename="note.txt"),
        [target("PARTIAL")],
    )

    assert isinstance(result, ReplacementError)
    assert result.message == "Only PDF, DOCX, and PPTX documents are currently supported for data replacement"
