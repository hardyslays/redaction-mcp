import base64

import fitz

from src.mcp.server import redact, redaction_mcp, replace
from src.server.models import RedactionRequest, ReplacementRequest


def test_mcp_tool_uses_shared_redaction_contract() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret")
    source = pdf.tobytes()
    pdf.close()

    request = RedactionRequest.model_validate(
        {
            "document": {"base64data": base64.b64encode(source).decode(), "filename": "source.pdf"},
            "targets": [{"type": "text", "values": ["secret"]}],
        }
    )
    result = redact(request)

    assert result.filename == "source-redacted.pdf"
    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    output.close()
    assert redaction_mcp.name == "redaction-mcp"


def test_mcp_replace_tool_uses_shared_replacement_contract() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret")
    source = pdf.tobytes()
    pdf.close()

    request = ReplacementRequest.model_validate({
        "document": {"base64data": base64.b64encode(source).decode(), "filename": "source.pdf"},
        "targets": [{"type": "text", "values": ["secret"], "replacement_type": "PARTIAL"}],
    })
    result = replace(request)

    output = fitz.open(stream=base64.b64decode(result.base64data), filetype="pdf")
    assert "secret" not in output[0].get_text()
    assert "****et" in output[0].get_text()
    output.close()
