import base64
import re
import sys
from types import SimpleNamespace
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf as fitz
import pytest
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.util import Inches

from redaction_mcp.core.engines.pdf_replacement import (
    _TextStyle,
    _background_color,
    _fontname,
    _replacement_lines,
    replacement_text,
)
from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.errors import ReplacementError
from redaction_mcp.core.models.replacement import TextReplacementTarget
from redaction_mcp.core.services.replacement_service import replace_document
from redaction_mcp.core.services.replacement_text import ReplacementContext, infer_entity_type


def pdf_with_text(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    data = pdf.tobytes()
    pdf.close()
    return data


def pdf_with_styled_text(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 100), text, fontsize=16, fontname="hebi", color=(0.2, 0.4, 0.6))
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


def test_partial_replacement_masks_non_whitespace_and_keeps_suffix() -> None:
    assert replacement_text("Jane Doe", target("PARTIAL")) == "**** *oe"
    assert replacement_text("AB CD", target("PARTIAL")) == "** CD"
    assert replacement_text("Jane\nDoe", target("PARTIAL")) == "****\n*oe"


def test_static_replacement_is_bracketed() -> None:
    assert replacement_text("Jane Doe", target("STATIC", static_text="REDACTED")) == "[REDACTED]"
    assert _replacement_lines("Long\nline", "[SAFE]", static=True) == ["[SA", "FE]"]


def test_replacement_size_guard_runs_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.services.replacement_service as service

    document = Document(base64data=base64.b64encode(b"oversize").decode(), filename="source.txt")
    monkeypatch.setattr(service, "DIRECT_LIMIT", 1)

    def decode(self: Document) -> bytes:
        raise AssertionError("Oversized content should not be decoded")

    monkeypatch.setattr(Document, "decoded_bytes", decode)
    result = replace_document(document, [target("PARTIAL", source="over")])

    assert isinstance(result, ReplacementError)
    assert "16 MiB" in result.message


def test_semantic_replacement_infers_structured_types_and_preserves_shape() -> None:
    context = ReplacementContext()
    email_target = target("SEMANTIC", source="jane@example.com")
    email = context.replacement_text("jane@example.com", email_target)
    assert infer_entity_type("jane@example.com") == "EMAIL"
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    assert abs(len(email) - len("jane@example.com")) <= max(1, round(len("jane@example.com") * 0.15))

    date_target = target("SEMANTIC", source="2020-01-31", entity_type="DATE")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", context.replacement_text("2020-01-31", date_target))


def test_optional_entity_pipeline_loads_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.services.replacement_text as service

    loads: list[int] = []

    def load(**kwargs: object):
        loads.append(1)
        return lambda value: SimpleNamespace(ents=[SimpleNamespace(label_="PERSON", text=value)])

    monkeypatch.setitem(sys.modules, "en_core_web_sm", SimpleNamespace(load=load))
    service._english_pipeline.cache_clear()
    try:
        assert service.infer_entity_type("Jane Doe") == "NAME"
        assert service.infer_entity_type("John Doe") == "NAME"
        assert loads == [1]
    finally:
        service._english_pipeline.cache_clear()


def test_semantic_replacement_is_stable_and_case_aware_within_context() -> None:
    context = ReplacementContext()
    semantic_target = target("SEMANTIC", source="JANE DOE", entity_type="NAME")
    first = context.replacement_text("JANE DOE", semantic_target)

    assert first == context.replacement_text("JANE DOE", semantic_target)
    assert first.upper() == first
    assert abs(len(first) - len("JANE DOE")) <= max(1, round(len("JANE DOE") * 0.15))

    title_case = context.replacement_text("Jane Doe", semantic_target)
    assert title_case == first.title()
    assert context.replacement_text("jane doe", semantic_target) == first.lower()


@pytest.mark.parametrize("source", ["May 1, 2020", "1/2/2020", "2020-1-2"])
def test_semantic_dates_keep_source_component_widths(source: str) -> None:
    generated = replacement_text(source, target("SEMANTIC", source=source, entity_type="DATE"))

    assert len(generated) == len(source)
    assert re.sub(r"[A-Za-z0-9]", "x", generated) == re.sub(r"[A-Za-z0-9]", "x", source)


def test_semantic_address_is_inferred_and_preserves_source_lines() -> None:
    source = "1234 Elm Street\nSpringfield"
    semantic_target = target("SEMANTIC", source=source)

    replacement = ReplacementContext().replacement_text(source, semantic_target)

    assert infer_entity_type(source) == "ADDRESS"
    assert infer_entity_type("1234 Elm Street Springfield") == "ADDRESS"
    assert len(replacement.splitlines()) == 2
    assert re.match(r"^\d+\s+", replacement.splitlines()[0])
    assert replacement != "0000 Xxx Xxxxxx\nXxxxxxxxxxx"


def test_semantic_name_and_short_address_preserve_source_shape() -> None:
    name = "Jane-Doe"
    generated_name = replacement_text(name, target("SEMANTIC", source=name, entity_type="NAME"))
    assert re.fullmatch(r"[A-Z][a-z]{3}-[A-Z][a-z]{2}", generated_name)
    assert generated_name != name

    address = "123 Main St"
    generated_address = replacement_text(address, target("SEMANTIC", source=address, entity_type="ADDRESS"))
    assert re.fullmatch(r"\d{3} [A-Z][a-z]{3} St", generated_address)
    assert generated_address != address

    place = "New-York"
    generated_place = replacement_text(place, target("SEMANTIC", source=place, entity_type="PLACE"))
    assert re.fullmatch(r"[A-Z][a-z]{2}-[A-Z][a-z]{3}", generated_place)


@pytest.mark.parametrize(
    ("source_font", "flags", "expected"),
    [
        ("Arial", 0, "helv"),
        ("Calibri-BoldItalic", 0, "hebi"),
        ("Times New Roman", 16, "tibo"),
        ("Courier New", 2, "coit"),
        ("Symbol", 0, "symbol"),
        ("ZapfDingbats", 0, "zapfdingbats"),
    ],
)
def test_pdf_font_mapping_handles_common_pdf_font_families(source_font: str, flags: int, expected: str) -> None:
    style = _TextStyle(12, source_font, flags, (0, 0, 0), fitz.Point(0, 0))
    assert _fontname(style) == expected


def test_pdf_background_colour_is_extracted_from_source_span_area() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    rect = fitz.Rect(72, 72, 144, 96)
    page.draw_rect(rect, color=None, fill=(0.1, 0.2, 0.3))

    assert _background_color(page, rect) == pytest.approx((0.1, 0.2, 0.3), abs=0.01)
    pdf.close()


def test_pdf_replacement_samples_background_only_for_matched_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.engines.pdf_replacement as engine

    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "secret visible")
    pdf.new_page().insert_text((72, 72), "ordinary text")
    source = Document(base64data=base64.b64encode(pdf.tobytes()).decode(), filename="source.pdf")
    pdf.close()
    indexed_pages: list[int] = []
    sampled_pages: list[int] = []
    original_index = engine._text_index_with_styles
    original_background = engine._background_color

    def index(page: fitz.Page):
        indexed_pages.append(page.number)
        return original_index(page)

    def background(page: fitz.Page, rect: fitz.Rect):
        sampled_pages.append(page.number)
        return original_background(page, rect)

    monkeypatch.setattr(engine, "_text_index_with_styles", index)
    monkeypatch.setattr(engine, "_background_color", background)
    result = replace_document(source, [
        target("STATIC", source="missing", static_text="SAFE"),
        target("STATIC", source="secret", static_text="SAFE"),
    ])

    assert isinstance(result, Document)
    assert indexed_pages == [0, 1]
    assert sampled_pages == [0]


def test_pdf_replacement_finishes_each_page_before_indexing_next(monkeypatch: pytest.MonkeyPatch) -> None:
    import redaction_mcp.core.engines.pdf_replacement as engine

    pdf = fitz.open()
    for _ in range(2):
        page = pdf.new_page()
        page.insert_text((72, 72), "secret")
        page.insert_text((72, 72), "public")
    source = Document(base64data=base64.b64encode(pdf.tobytes()).decode(), filename="source.pdf")
    pdf.close()
    events: list[tuple[str, int]] = []
    original_index = engine._text_index_with_styles
    original_restore = engine._restore_characters

    def index(page: fitz.Page):
        events.append(("index", page.number))
        return original_index(page)

    def restore(page: fitz.Page, *args: object):
        events.append(("restore", page.number))
        return original_restore(page, *args)

    monkeypatch.setattr(engine, "_text_index_with_styles", index)
    monkeypatch.setattr(engine, "_restore_characters", restore)
    result = replace_document(source, [target("STATIC", source="secret", static_text="SAFE")])

    assert isinstance(result, Document)
    assert events == [("index", 0), ("restore", 0), ("index", 1), ("restore", 1)]


def test_regex_replacement_preserves_character_pattern_without_source_text() -> None:
    assert replacement_text("AB-129 z", target("REGEX")) == "BC-230 a"


def test_replacement_fallbacks_do_not_return_unicode_source_letters() -> None:
    source = "Élodie 张伟 ١٢"
    regex_value = replacement_text(source, target("REGEX", source=source))
    generic_value = replacement_text(source, target("SEMANTIC", source=source, entity_type="GENERIC"))

    assert regex_value != source
    assert generic_value != source
    assert "张伟" not in regex_value and "张伟" not in generic_value
    assert regex_value[6] == generic_value[6] == " "


def test_semantic_fallback_changes_already_dummy_shaped_source() -> None:
    source = "xxx-000"
    assert replacement_text(source, target("SEMANTIC", source=source, entity_type="GENERIC")) != source


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


def test_pdf_replacement_removes_every_styled_span_of_one_match() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Jane ", fontname="helv")
    next_x = 72 + fitz.get_text_length("Jane ", fontname="helv", fontsize=11)
    page.insert_text((next_x, 72), "Doe", fontname="hebo")
    source = Document(base64data=base64.b64encode(pdf.tobytes()).decode(), filename="source.pdf")
    pdf.close()

    result = replace_document(source, [target("STATIC", source="Jane Doe", static_text="SAFE")])

    assert isinstance(result, Document)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    text = output[0].get_text()
    assert "Jane" not in text and "Doe" not in text
    assert "[SAFE]" in text
    output.close()


def test_pdf_replacement_preserves_source_baseline_font_style_size_and_colour() -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(pdf_with_styled_text("ID AB-129")).decode(), filename="source.pdf"),
        [target("REGEX", source="AB-129")],
    )

    assert not isinstance(result, ReplacementError)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    spans = [
        span
        for block in output[0].get_text("rawdict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
    ]
    replacement = next(span for span in spans if "".join(char["c"] for char in span["chars"]) == "BC-230")
    assert replacement["font"] == "Helvetica-BoldOblique"
    assert replacement["size"] == pytest.approx(16)
    assert replacement["color"] == 0x336699
    assert replacement["origin"][1] == pytest.approx(100)
    output.close()


def test_pdf_replacement_preserves_rotation_and_overlapping_non_target_text() -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    origin = fitz.Point(150, 220)
    page.insert_text(origin, "secret", fontsize=24, morph=(origin, fitz.Matrix(45)))
    page.insert_text((136, 170), "K", fontsize=10)
    data = pdf.tobytes()
    pdf.close()

    result = replace_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [target("STATIC", source="secret", static_text="SAFE")],
    )

    assert not isinstance(result, ReplacementError)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    assert output[0].get_text().count("K") == 1
    lines = [line for block in output[0].get_text("rawdict")["blocks"] for line in block.get("lines", [])]
    replacement = next(
        line for line in lines if "[SAFE]" in "".join(char["c"] for span in line["spans"] for char in span["chars"])
    )
    assert replacement["dir"] == pytest.approx((2 ** -0.5, -(2 ** -0.5)), abs=0.01)
    output.close()


def test_pdf_semantic_replacement_handles_multiline_address_from_single_line_target() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "1234 Elm Street")
    page.insert_text((72, 96), "Springfield")
    data = pdf.tobytes()
    pdf.close()

    result = replace_document(
        Document(base64data=base64.b64encode(data).decode(), filename="source.pdf"),
        [target("SEMANTIC", source="1234 Elm Street Springfield")],
    )

    assert not isinstance(result, ReplacementError)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    lines = [line.strip() for line in output[0].get_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0] != "1234 Elm Street"
    assert lines[1] != "Springfield"
    assert re.match(r"^\d+\s+", lines[0])
    output.close()


def test_pdf_replacement_ignore_case_is_opt_in() -> None:
    source = Document(base64data=base64.b64encode(pdf_with_text("Owner JANE DOE")).decode(), filename="source.pdf")

    case_sensitive = replace_document(source, [target("STATIC", static_text="SAFE")])
    assert not isinstance(case_sensitive, ReplacementError)
    output = fitz.open(stream=base64.b64decode(case_sensitive.base64data), filetype="pdf")
    assert "JANE DOE" in output[0].get_text()
    output.close()

    result = replace_document(source, [target("STATIC", static_text="SAFE", ignore_case=True)])
    assert not isinstance(result, ReplacementError)
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "JANE DOE" not in output[0].get_text()
    assert "[SAFE]" in output[0].get_text()
    output.close()


@pytest.mark.parametrize("extension", ["pdf", "docx", "pptx"])
def test_replacement_requires_alphanumeric_boundaries_by_default(extension: str) -> None:
    source = "john johnathon"
    builders = {"pdf": pdf_with_text, "docx": docx_with_text, "pptx": pptx_with_text}
    document = Document(base64data=base64.b64encode(builders[extension](source)).decode(), filename=f"source.{extension}")

    safe_result = replace_document(document, [target("STATIC", source="john", static_text="SAFE")])
    assert not isinstance(safe_result, ReplacementError)
    safe_text = output_text(safe_result, extension)
    assert "johnathon" in safe_text
    assert safe_text.count("[SAFE]") == 1

    partial_result = replace_document(
        document, [target("STATIC", source="john", static_text="SAFE", partial_match=True)]
    )
    assert not isinstance(partial_result, ReplacementError)
    assert "johnathon" not in output_text(partial_result, extension)


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


def test_pptm_replacement_removes_matching_text() -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(pptm_with_text("Owner Jane Doe")).decode(), filename="source.pptm"),
        [target("STATIC", static_text="SAFE")],
    )

    assert not isinstance(result, ReplacementError)
    output = Presentation(BytesIO(base64.b64decode(result.base64data)))
    assert "Jane Doe" not in output.slides[0].shapes[0].text
    assert "[SAFE]" in output.slides[0].shapes[0].text


def test_docx_and_pptx_replacement_ignore_case() -> None:
    docx_result = replace_document(
        Document(base64data=base64.b64encode(docx_with_text("Owner JANE DOE")).decode(), filename="source.docx"),
        [target("STATIC", static_text="SAFE", ignore_case=True)],
    )
    assert not isinstance(docx_result, ReplacementError)
    docx = DocxDocument(BytesIO(base64.b64decode(docx_result.base64data)))
    assert "JANE DOE" not in docx.paragraphs[0].text
    assert "[SAFE]" in docx.paragraphs[0].text

    pptx_result = replace_document(
        Document(base64data=base64.b64encode(pptx_with_text("Owner JANE DOE")).decode(), filename="source.pptx"),
        [target("STATIC", static_text="SAFE", ignore_case=True)],
    )
    assert not isinstance(pptx_result, ReplacementError)
    pptx = Presentation(BytesIO(base64.b64decode(pptx_result.base64data)))
    assert "JANE DOE" not in pptx.slides[0].shapes[0].text
    assert "[SAFE]" in pptx.slides[0].shapes[0].text


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


def test_csv_replacement_is_supported() -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(b"text").decode(), filename="note.csv"),
        [target("PARTIAL")],
    )

    assert isinstance(result, Document)


@pytest.mark.parametrize(
    ("kind", "expected", "kwargs"),
    [
        ("PARTIAL", "**** *oe", {}),
        ("STATIC", "[SAFE]", {"static_text": "SAFE"}),
        ("REGEX", "Kbof Epf", {}),
    ],
)
def test_txt_replacement_supports_all_strategies(kind: str, expected: str, kwargs: dict[str, str]) -> None:
    result = replace_document(
        Document(base64data=base64.b64encode(b"Owner Jane Doe").decode(), filename="source.txt"),
        [target(kind, **kwargs)],
    )

    assert isinstance(result, Document)
    assert result.filename == "source-replaced.txt"
    assert result.mime_type == "text/plain"
    output = base64.b64decode(result.base64data).decode()
    assert "Jane Doe" not in output
    assert expected in output


def test_txt_replacement_accepts_page_zero_and_rejects_other_pages() -> None:
    source = Document(base64data=base64.b64encode(b"Jane Doe").decode(), filename="source.txt")

    valid = replace_document(source, [TextReplacementTarget(type="text", values=["Jane Doe"], replacement_type="PARTIAL", pages=[0])])
    assert isinstance(valid, Document)

    invalid = replace_document(source, [TextReplacementTarget(type="text", values=["Jane Doe"], replacement_type="PARTIAL", pages=[1])])
    assert isinstance(invalid, ReplacementError)
    assert invalid.message == "TXT data replacement supports only page index 0"
