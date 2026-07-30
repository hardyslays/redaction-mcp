import asyncio
import base64
import json
import sys
from pathlib import Path

import fitz
import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport

from src.mcp.models import McpDocument
from src.mcp.server import create_mcp_app, redaction_mcp


def _pdf_base64() -> str:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret visible")
    data = pdf.tobytes()
    pdf.close()
    return base64.b64encode(data).decode()


def _document() -> dict[str, str]:
    return {"base64data": _pdf_base64(), "filename": "source.pdf"}


def test_mcp_tools_publish_flat_agent_facing_contract() -> None:
    async def verify() -> None:
        tools = {tool.name: tool for tool in await redaction_mcp.list_tools()}
        assert set(tools) == {"inspect_document", "redact", "replace"}
        assert tools["redact"].parameters["required"] == ["document", "targets"]
        assert "request" not in tools["redact"].parameters["properties"]
        assert tools["redact"].output_schema is not None

    asyncio.run(verify())


def test_mcp_document_only_accepts_portable_base64_input() -> None:
    with pytest.raises(ValueError):
        McpDocument.model_validate({"path": "/private/document.pdf"})


def test_stdio_mcp_initializes_discovers_and_invokes_tools() -> None:
    async def verify() -> None:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "src.cli.main"],
            cwd=str(Path(__file__).parents[1]),
            keep_alive=False,
        )
        async with Client(transport, init_timeout=10) as client:
            assert {tool.name for tool in await client.list_tools()} == {"inspect_document", "redact", "replace"}
            inspection = await client.call_tool("inspect_document", {"document": _document()}, timeout=10)
            assert inspection.data.pages[0].text == "secret visible\n"
            result = await client.call_tool(
                "redact",
                {"document": _document(), "targets": [{"type": "text", "values": ["secret"]}]},
                timeout=10,
            )
            output = fitz.open(stream=base64.b64decode(result.data.base64data), filetype="pdf")
            assert "secret" not in output[0].get_text()
            output.close()
            replacement = await client.call_tool(
                "replace",
                {
                    "document": _document(),
                    "targets": [{"type": "text", "values": ["secret"], "replacement_type": "STATIC", "static_text": "SAFE"}],
                },
                timeout=10,
            )
            output = fitz.open(stream=base64.b64decode(replacement.data.base64data), filetype="pdf")
            assert "[SAFE]" in output[0].get_text()
            output.close()

    asyncio.run(verify())


def test_http_mcp_initializes_discovers_and_invokes_tools() -> None:
    async def verify() -> None:
        app = create_mcp_app()
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                initialize = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                    },
                )
                assert initialize.status_code == 200
                session_id = initialize.headers["mcp-session-id"]
                session_headers = headers | {"Mcp-Session-Id": session_id}
                listed = await client.post(
                    "/mcp",
                    headers=session_headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                tools = _sse_data(listed)["result"]["tools"]
                assert {tool["name"] for tool in tools} == {"inspect_document", "redact", "replace"}
                called = await client.post(
                    "/mcp",
                    headers=session_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "inspect_document", "arguments": {"document": _document()}},
                    },
                )
                result = _sse_data(called)["result"]
                assert result["structuredContent"]["pages"][0]["text"] == "secret visible\n"
                redacted = await client.post(
                    "/mcp",
                    headers=session_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "redact", "arguments": {"document": _document(), "targets": [{"type": "text", "values": ["secret"]}]}},
                    },
                )
                redacted_data = _sse_data(redacted)["result"]["structuredContent"]
                output = fitz.open(stream=base64.b64decode(redacted_data["base64data"]), filetype="pdf")
                assert "secret" not in output[0].get_text()
                output.close()
                replaced = await client.post(
                    "/mcp",
                    headers=session_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "replace",
                            "arguments": {
                                "document": _document(),
                                "targets": [{"type": "text", "values": ["secret"], "replacement_type": "STATIC", "static_text": "SAFE"}],
                            },
                        },
                    },
                )
                replaced_data = _sse_data(replaced)["result"]["structuredContent"]
                output = fitz.open(stream=base64.b64decode(replaced_data["base64data"]), filetype="pdf")
                assert "[SAFE]" in output[0].get_text()
                output.close()

    asyncio.run(verify())


def test_http_mcp_bearer_token_protects_the_endpoint() -> None:
    async def verify() -> None:
        app = create_mcp_app("test-token")
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.post("/mcp", json={})
                assert response.status_code == 401
                response = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer test-token",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                    },
                )
                assert response.status_code == 200

    asyncio.run(verify())


def _sse_data(response: httpx.Response) -> dict:
    assert response.status_code == 200
    return json.loads(response.text.split("data: ", maxsplit=1)[1])
