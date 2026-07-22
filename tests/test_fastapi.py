import base64
import asyncio

import fitz
import httpx

from src.server.main import app


def sample_pdf() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret")
    data = pdf.tobytes()
    pdf.close()
    return data


def request(method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_health() -> None:
    response = request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_redact_accepts_base64_pdf_and_returns_base64_pdf() -> None:
    payload = {
        "document": {"base64data": base64.b64encode(sample_pdf()).decode(), "filename": "input.pdf"},
        "targets": [{"type": "text", "values": ["secret"]}],
    }
    response = request("POST", "/redact", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "input-redacted.pdf"
    result = fitz.open(stream=base64.b64decode(body["base64data"]), filetype="pdf")
    assert "secret" not in result[0].get_text()
    result.close()


def test_redact_rejects_non_base64_document_data() -> None:
    response = request(
        "POST",
        "/redact",
        json={"document": {"base64data": "not base64"}, "targets": [{"type": "text", "values": ["x"]}]},
    )

    assert response.status_code == 422
