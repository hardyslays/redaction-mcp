import base64
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf as fitz
import pytest
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.util import Inches

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.errors import RedactionError
from redaction_mcp.core.models.redaction import BoundingBox, BoundingBoxTarget, PageTarget, RedactionOptions, RegexTarget, TextTarget
from redaction_mcp.core.services.redaction_service import redact_document


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


def pptm_with_text(text: str) -> bytes:
    source = BytesIO(pptx_with_text(text))
    output = BytesIO()
    with ZipFile(source) as archive, ZipFile(output, "w", ZIP_DEFLATED) as replacement:
        for entry in archive.infolist():
            data = archive.read(entry.filename)
            if entry.filename == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                    b"application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
                )
            replacement.writestr(entry, data)
    return output.getvalue()


def output_text(result: Document, extension: str) -> str:
    data = base64.b64decode(result.base64data)
    if extension == "pdf":
        output = fitz.open(stream=data, filetype="pdf")
        try:
            return output[0].get_text()
        finally:
            output.close()
    if extension == "docx":
        return DocxDocument(BytesIO(data)).paragraphs[0].text
    return Presentation(BytesIO(data)).slides[0].shapes[0].text


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


def test_pdf_redaction_reuses_page_text_for_multiple_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.engines.pdf_redaction as engine

    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "secret visible")
    pdf.new_page().insert_text((72, 72), "ordinary text")
    source = Document(base64data=base64.b64encode(pdf.tobytes()).decode(), filename="source.pdf")
    pdf.close()
    indexed_pages: list[int] = []
    rendered_pages: list[int] = []
    original_index = engine._text_index
    original_render = engine._renderable_characters

    def index(page: fitz.Page):
        indexed_pages.append(page.number)
        return original_index(page)

    def render(page: fitz.Page):
        rendered_pages.append(page.number)
        return original_render(page)

    monkeypatch.setattr(engine, "_text_index", index)
    monkeypatch.setattr(engine, "_renderable_characters", render)
    result = redact_document(source, [
        TextTarget(type="text", values=["missing"]),
        TextTarget(type="text", values=["secret"]),
    ])

    assert isinstance(result, Document)
    assert indexed_pages == [0, 1]
    assert rendered_pages == [0]  # untouched pages and pages without restoration work are skipped


def test_pdf_redaction_finishes_each_page_before_indexing_next(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.engines.pdf_redaction as engine

    pdf = fitz.open()
    for _ in range(2):
        page = pdf.new_page()
        page.insert_text((72, 72), "secret")
        page.insert_text((72, 72), "public")
    source = Document(base64data=base64.b64encode(pdf.tobytes()).decode(), filename="source.pdf")
    pdf.close()
    events: list[tuple[str, int]] = []
    original_index = engine._text_index
    original_restore = engine._restore_characters

    def index(page: fitz.Page):
        events.append(("index", page.number))
        return original_index(page)

    def restore(page: fitz.Page, *args: object):
        events.append(("restore", page.number))
        return original_restore(page, *args)

    monkeypatch.setattr(engine, "_text_index", index)
    monkeypatch.setattr(engine, "_restore_characters", restore)
    result = redact_document(source, [TextTarget(type="text", values=["secret"])])

    assert isinstance(result, Document)
    assert events == [("index", 0), ("restore", 0), ("index", 1), ("restore", 1)]


def test_pdf_restoration_candidates_require_positive_overlap() -> None:
    from redaction_mcp.core.engines.pdf_redaction import _RenderableCharacter, _quad_from_rect, _restoration_candidates

    quad = _quad_from_rect(fitz.Rect(10, 10, 20, 20))

    def character(index: int, rect: fitz.Rect) -> _RenderableCharacter:
        return _RenderableCharacter(
            value="x", rect=rect, quad=_quad_from_rect(rect), origin=fitz.Point(0, 0),
            size=12, font="Helvetica", flags=0, color=(0, 0, 0),
            direction=(1, 0), text_index=index, sequence=index,
        )

    characters = [
        character(0, fitz.Rect(0, 0, 10, 10)),
        character(1, fitz.Rect(19, 19, 25, 25)),
        character(2, fitz.Rect(12, 12, 18, 18)),
    ]

    assert [item.text_index for item in _restoration_candidates(characters, quad, frozenset({2}))] == [1]


def test_text_redaction_ignore_case_is_opt_in_for_pdf() -> None:
    source = Document(base64data=base64.b64encode(pdf_with_text("SECRET visible")).decode(), filename="source.pdf")

    case_sensitive = redact_document(source, [TextTarget(type="text", values=["secret"])])
    assert isinstance(case_sensitive, Document)
    output = fitz.open(stream=base64.b64decode(case_sensitive.base64data), filetype="pdf")
    assert "SECRET" in output[0].get_text()
    output.close()

    result = redact_document(source, [TextTarget(type="text", values=["secret"], ignore_case=True)])
    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "SECRET" not in output[0].get_text()
    output.close()


@pytest.mark.parametrize("extension", ["pdf", "docx", "pptx"])
def test_text_redaction_requires_alphanumeric_boundaries_by_default(extension: str) -> None:
    source = "john johnathon"
    builders = {"pdf": pdf_with_text, "docx": docx_with_text, "pptx": pptx_with_text}
    document = Document(base64data=base64.b64encode(builders[extension](source)).decode(), filename=f"source.{extension}")

    safe_result = redact_document(document, [TextTarget(type="text", values=["john"])])
    assert isinstance(safe_result, Document)
    safe_text = output_text(safe_result, extension)
    assert "johnathon" in safe_text
    assert "john " not in safe_text

    partial_result = redact_document(document, [TextTarget(type="text", values=["john"], partial_match=True)])
    assert isinstance(partial_result, Document)
    assert "johnathon" not in output_text(partial_result, extension)


def test_normalized_bounding_box_redacts_area() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pdf_with_text("hide")).decode(), filename="source.pdf"),
        [BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0, y=0, width=1, height=1)])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert output[0].get_text().strip() == ""
    output.close()


def test_csv_document_is_supported() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(b"plain text").decode(), filename="note.csv"),
        [TextTarget(type="text", values=["text"])],
    )

    assert isinstance(result, Document)
    assert base64.b64decode(result.base64data).decode().strip() == "plain ****"


def test_txt_text_redaction_replaces_matching_text_with_asterisks() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(b"secret visible").decode(), filename="source.txt"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    assert result.filename == "source-redacted.txt"
    assert result.mime_type == "text/plain"
    assert base64.b64decode(result.base64data) == b"****** visible"


def test_direct_size_guard_runs_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.services.redaction_service as service

    document = Document(base64data=base64.b64encode(b"oversize").decode(), filename="source.txt")
    monkeypatch.setattr(service, "DIRECT_LIMIT", 1)

    def decode(self: Document) -> bytes:
        raise AssertionError("Oversized content should not be decoded")

    monkeypatch.setattr(Document, "decoded_bytes", decode)
    result = redact_document(document, [TextTarget(type="text", values=["over"])])

    assert isinstance(result, RedactionError)
    assert "16 MiB" in result.message


def test_document_base64_validation_uses_bounded_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.models.document as document_module

    encoded = base64.b64encode(b"x" * 786_433).decode()
    decode = document_module.base64.b64decode
    chunk_sizes: list[int] = []

    def record_chunk(value: str, *, validate: bool) -> bytes:
        chunk_sizes.append(len(value))
        return decode(value, validate=validate)

    monkeypatch.setattr(document_module.base64, "b64decode", record_chunk)
    Document(base64data=encoded)

    assert len(chunk_sizes) == 2
    assert max(chunk_sizes) <= 1024 * 1024
    with pytest.raises(ValueError, match="base64data must be valid base64"):
        Document(base64data="A" * (1024 * 1024 - 4) + "====" + "AAAA")


def test_txt_multiline_text_redaction_keeps_line_breaks() -> None:
    source = Document(base64data=base64.b64encode(b"Jane\nDoe public").decode(), filename="source.txt")
    result = redact_document(source, [TextTarget(type="text", values=["Jane Doe"])])

    assert isinstance(result, Document)
    assert base64.b64decode(result.base64data) == b"****\n*** public"


def test_txt_regex_only_first_match_keeps_whitespace() -> None:
    source = Document(base64data=base64.b64encode(b"AA 12\nAA 34").decode(), filename="source.txt")
    result = redact_document(source, [RegexTarget(
        type="regex", patterns=[r"AA\s+\d+"], only_first_match=True,
    )])

    assert isinstance(result, Document)
    assert base64.b64decode(result.base64data) == b"** **\nAA 34"


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_office_multiline_text_redaction_keeps_line_breaks(extension: str) -> None:
    builders = {"docx": docx_with_text, "pptx": pptx_with_text}
    source = Document(
        base64data=base64.b64encode(builders[extension]("Jane\nDoe public")).decode(),
        filename=f"source.{extension}",
    )
    result = redact_document(source, [TextTarget(type="text", values=["Jane Doe"])])

    assert isinstance(result, Document)
    assert output_text(result, extension) == "****\n*** public"


def test_txt_page_redaction_masks_text_and_preserves_whitespace_layout() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(b"Name: Jane Doe\n\tID 42\n").decode(), filename="source.txt"),
        [PageTarget(type="page", values=[0])],
    )

    assert isinstance(result, Document)
    assert base64.b64decode(result.base64data) == b"***** **** ***\n\t** **\n"


@pytest.mark.parametrize(
    ("target", "message"),
    [
        (PageTarget(type="page", values=[1]), "TXT redaction supports only page index 0"),
        (
            BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0, y=0, width=1, height=1)]),
            "TXT redaction does not support bounding-box targets",
        ),
        (TextTarget(type="text", values=["secret"], pages=[1]), "TXT redaction supports only page index 0"),
    ],
)
def test_txt_redaction_rejects_unsupported_targets_or_pages(target: object, message: str) -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(b"secret").decode(), filename="source.txt"), [target]
    )

    assert isinstance(result, RedactionError)
    assert result.message == message


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


def test_pptm_text_redaction_removes_matching_text() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pptm_with_text("secret visible")).decode(), filename="source.pptm"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert "secret" not in output.slides[0].shapes[0].text


def test_docx_and_pptx_text_redaction_ignore_case() -> None:
    docx_result = redact_document(
        Document(base64data=base64.b64encode(docx_with_text("SECRET visible")).decode(), filename="source.docx"),
        [TextTarget(type="text", values=["secret"], ignore_case=True)],
    )
    assert isinstance(docx_result, Document)
    docx = DocxDocument(BytesIO(base64.b64decode(docx_result.base64data)))
    assert "SECRET" not in docx.paragraphs[0].text

    pptx_result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text("SECRET visible")).decode(), filename="source.pptx"),
        [TextTarget(type="text", values=["secret"], ignore_case=True)],
    )
    assert isinstance(pptx_result, Document)
    pptx = Presentation(BytesIO(base64.b64decode(pptx_result.base64data)))
    assert "SECRET" not in pptx.slides[0].shapes[0].text


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


def test_docx_bounding_box_redacts_only_intersecting_words() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(docx_with_text("secret visible")).decode(), filename="source.docx"),
        [BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0, y=0, width=0.5, height=1)])],
    )

    assert isinstance(result, Document)
    assert output_text(result, "docx") == "****** visible"


def test_pptx_bounding_box_redacts_only_intersecting_words() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text("secret visible")).decode(), filename="source.pptx"),
        [BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0.07, y=0.07, width=0.16, height=0.14)])],
    )

    assert isinstance(result, Document)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert len(output.slides[0].shapes) == 1
    assert output.slides[0].shapes[0].text == "****** visible"


@pytest.mark.parametrize(
    "values",
    [
        {"page": 0, "x": 0, "y": 0, "width": 1, "height": 1, "units": "pixels"},
        {"page": 0, "x": 0.8, "y": 0, "width": 0.3, "height": 1},
    ],
)
def test_bounding_box_rejects_non_normalized_or_out_of_page_values(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        BoundingBox.model_validate(values)


def test_pptx_page_redaction_removes_all_slide_shapes() -> None:
    result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text()).decode(), filename="source.pptx"),
        [PageTarget(type="page", values=[0])],
    )

    assert isinstance(result, Document)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert len(output.slides[0].shapes) == 0


def test_pdf_regex_redacts_exact_multiline_span_without_redacting_surrounding_text() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Owner: Jane")
    page.insert_text((72, 96), "Doe; public")
    data = pdf.tobytes()
    pdf.close()

    result = redact_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [RegexTarget(type="regex", patterns=[r"Jane\s+Doe"], ignore_case=False)],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    text = output[0].get_text()
    assert "Jane" not in text and "Doe" not in text
    assert "Owner:" in text and "public" in text
    output.close()


def test_regex_only_first_match_is_document_wide_for_pdf_and_pptx() -> None:
    pdf = fitz.open()
    first = pdf.new_page()
    first.insert_text((72, 72), "ID AB123456")
    second = pdf.new_page()
    second.insert_text((72, 72), "ID CD234567")
    data = pdf.tobytes()
    pdf.close()
    target = RegexTarget(type="regex", patterns=[r"[A-Z]{2}\d{6}"], ignore_case=False, only_first_match=True)

    result = redact_document(Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"), [target])
    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "AB123456" not in output[0].get_text()
    assert "CD234567" in output[1].get_text()
    output.close()

    pptx_result = redact_document(
        Document(base64data=base64.b64encode(pptx_with_text("AB123456 AB123456")).decode(), filename="source.pptx"),
        [target],
    )
    assert isinstance(pptx_result, Document)
    presentation = Presentation(BytesIO(base64.b64decode(pptx_result.base64data)))
    assert presentation.slides[0].shapes[0].text.count("AB123456") == 1


def test_pptx_regex_only_first_match_stops_before_later_slides() -> None:
    presentation = Presentation()
    for value in ("ID AB123456", "ID CD234567"):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = value
    output = BytesIO()
    presentation.save(output)
    source = Document(base64data=base64.b64encode(output.getvalue()).decode(), filename="source.pptx")

    result = redact_document(source, [RegexTarget(
        type="regex", patterns=[r"[A-Z]{2}\d{6}"], ignore_case=False, only_first_match=True,
    )])

    assert isinstance(result, Document)
    slides = Presentation(BytesIO(base64.b64decode(result.base64data))).slides
    assert "AB123456" not in slides[0].shapes[0].text
    assert "CD234567" in slides[1].shapes[0].text


def test_pdf_restores_small_overlapping_text_drawn_above_target() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret", fontsize=12)
    page.insert_text((112, 76), "Z", fontsize=12, rotate=90)
    data = pdf.tobytes()
    pdf.close()

    result = redact_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    assert output[0].get_text().count("Z") == 1
    output.close()


@pytest.mark.parametrize("target_drawn_first", [True, False])
def test_pdf_restores_fully_overlapping_non_target_text(target_drawn_first: bool) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    values = ("secret", "public") if target_drawn_first else ("public", "secret")
    for value in values:
        page.insert_text((72, 72), value, fontsize=12)
    data = pdf.tobytes()
    pdf.close()

    result = redact_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    assert output[0].get_text().count("public") == 1
    output.close()


@pytest.mark.parametrize("area_target", [
    PageTarget(type="page", values=[0]),
    BoundingBoxTarget(type="bounding_box", values=[BoundingBox(page=0, x=0, y=0, width=1, height=1)]),
])
def test_pdf_area_redaction_does_not_restore_overlapping_text(area_target: object) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret")
    page.insert_text((72, 72), "public")
    source = Document(base64data=base64.b64encode(pdf.tobytes()).decode(), filename="source.pdf")
    pdf.close()

    result = redact_document(source, [TextTarget(type="text", values=["secret"]), area_target])

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert output[0].get_text() == ""
    output.close()


def test_pdf_text_redaction_insets_annotation_within_text_region() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret", fontsize=12)
    data = pdf.tobytes()
    pdf.close()

    result = redact_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    fills = [drawing for drawing in output[0].get_drawings() if drawing["fill"] == (0.0, 0.0, 0.0)]
    assert len(fills) == 1
    assert fills[0]["rect"].height == pytest.approx(11, abs=0.1)  # 12 pt em minus 0.5 pt on each side.
    output.close()


def test_pdf_rotated_redaction_uses_rotated_fill_and_restores_non_target_text() -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    origin = fitz.Point(150, 220)
    page.insert_text(origin, "secret", fontsize=24, morph=(origin, fitz.Matrix(45)))
    # This character is inside the redaction annotation's axis-aligned bounds,
    # but outside most of the actual rotated text quadrilateral.
    page.insert_text((136, 170), "K", fontsize=10)
    data = pdf.tobytes()
    pdf.close()

    result = redact_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [TextTarget(type="text", values=["secret"])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    assert output[0].get_text().count("K") == 1
    fill = next(drawing for drawing in output[0].get_drawings() if drawing["fill"] == (0.0, 0.0, 0.0))
    first_edge = fill["items"][0]
    assert first_edge[0] == "l"
    assert abs(first_edge[1].x - first_edge[2].x) > 1
    assert abs(first_edge[1].y - first_edge[2].y) > 1
    output.close()


def test_pdf_rotated_inline_target_restores_every_adjacent_glyph() -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    origin = fitz.Point(65, 230)
    page.insert_text(origin, "Rotated 45 degrees", fontsize=28, morph=(origin, fitz.Matrix(45)))

    source_characters = [
        (char["c"], fitz.Point(char["origin"]))
        for block in page.get_text("rawdict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        for char in span["chars"]
    ]
    data = pdf.tobytes()
    pdf.close()

    result = redact_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [TextTarget(type="text", values=["45"])],
    )

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    output_characters = [
        (char["c"], fitz.Point(char["origin"]))
        for block in output[0].get_text("rawdict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        for char in span["chars"]
    ]
    for index, (value, expected_origin) in enumerate(source_characters):
        if index in {8, 9}:  # The requested "45" target.
            assert not any(value == actual and abs(point - expected_origin) < 0.1 for actual, point in output_characters)
        else:
            assert sum(
                value == actual and abs(point - expected_origin) < 0.1
                for actual, point in output_characters
            ) == 1
    output.close()


def test_direct_processing_enforces_decoded_size_limit(monkeypatch) -> None:
    import redaction_mcp.core.services.redaction_service as service

    monkeypatch.setattr(service, "DIRECT_LIMIT", 3)
    result = redact_document(
        Document(base64data=base64.b64encode(b"four").decode(), filename="source.txt"),
        [TextTarget(type="text", values=["four"])],
    )

    assert isinstance(result, RedactionError)
    assert result.message == "Direct requests are limited to 16 MiB; use the job API"
