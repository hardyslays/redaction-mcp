import base64
import asyncio
import importlib
import threading
import time

import pymupdf as fitz
import httpx
import pytest

from redaction_mcp.server.main import app, main
from redaction_mcp.core.services.jobs import reset_job_manager
from redaction_mcp.core.models.document import Document


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


def test_fastapi_requires_token_for_non_loopback_bind(monkeypatch) -> None:
    monkeypatch.setenv("FASTAPI_HOST", "0.0.0.0")
    monkeypatch.delenv("FASTAPI_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="FASTAPI_AUTH_TOKEN"):
        main()


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


def test_direct_processing_does_not_block_health_request(monkeypatch: pytest.MonkeyPatch) -> None:
    server = importlib.import_module("redaction_mcp.server.main")
    started = threading.Event()
    finish = threading.Event()

    def slow_redaction(document: Document, *args: object) -> Document:
        started.set()
        finish.wait(timeout=1)
        return document

    monkeypatch.setattr(server, "redact_document", slow_redaction)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "document": {"base64data": base64.b64encode(b"sample").decode(), "filename": "sample.txt"},
                "targets": [{"type": "text", "values": ["sample"]}],
            }
            processing = asyncio.create_task(client.post("/redact", json=payload))
            try:
                for _ in range(50):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert started.is_set()
                start = time.monotonic()
                health = await client.get("/health")
                assert health.status_code == 200
                assert time.monotonic() - start < 0.5
            finally:
                finish.set()
            assert (await processing).status_code == 200

    asyncio.run(exercise())


def test_redact_rejects_non_base64_document_data() -> None:
    response = request(
        "POST",
        "/redact",
        json={"document": {"base64data": "not base64"}, "targets": [{"type": "text", "values": ["x"]}]},
    )

    assert response.status_code == 422


def test_redact_rejects_polygon_target() -> None:
    response = request(
        "POST",
        "/redact",
        json={
            "document": {"base64data": base64.b64encode(sample_pdf()).decode()},
            "targets": [{"type": "polygon", "values": []}],
        },
    )

    assert response.status_code == 422


def test_replace_accepts_base64_pdf_and_returns_replaced_pdf() -> None:
    payload = {
        "document": {"base64data": base64.b64encode(sample_pdf()).decode(), "filename": "input.pdf"},
        "targets": [{"type": "text", "values": ["secret"], "replacement_type": "STATIC", "static_text": "SAFE"}],
    }
    response = request("POST", "/replace", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "input-replaced.pdf"
    result = fitz.open(stream=base64.b64decode(body["base64data"]), filetype="pdf")
    assert "secret" not in result[0].get_text()
    assert "[SAFE]" in result[0].get_text()
    result.close()


def test_chunked_fastapi_job_workflow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REDACTION_STORAGE_PATH", str(tmp_path))
    reset_job_manager()
    data = b"name,email\nJane Doe,jane@example.com\n"
    created = request("POST", "/uploads", json={"filename": "people.csv", "size_bytes": len(data)})
    assert created.status_code == 201
    upload_id = created.json()["upload_id"]
    uploaded = request("PUT", f"/uploads/{upload_id}/chunks/0", content=data)
    assert uploaded.status_code == 200
    submitted = request(
        "POST",
        "/jobs",
        json={"upload_id": upload_id, "operation": "redact", "targets": [{"type": "text", "values": ["Jane Doe"]}]},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    deadline = time.monotonic() + 5
    status = submitted.json()
    while status["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)
        status = request("GET", f"/jobs/{job_id}").json()
    assert status["status"] == "succeeded"
    result = request("GET", f"/jobs/{job_id}/result", params={"offset": 0, "limit": 1024})
    assert result.content == b"name,email\n**** ***,jane@example.com\n"
    assert request("DELETE", f"/jobs/{job_id}").status_code == 200
    reset_job_manager()
