"""FastMCP server sharing the exact same redaction request contract as FastAPI."""

from __future__ import annotations

from src.core.models.errors import RedactionError, ReplacementError
from src.core.services.redaction_service import redact_document
from src.core.services.replacement_service import replace_document
from src.server.models import RedactionRequest, RedactionResponse, ReplacementRequest, ReplacementResponse

try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - dependency installation error
    raise RuntimeError("Install the 'fastmcp' dependency to run the MCP server") from exc


redaction_mcp = FastMCP("redaction-mcp")


@redaction_mcp.tool()
def redact(request: RedactionRequest) -> RedactionResponse:
    """Permanently redact selected content from a PDF.

    Page numbers are zero-based. Input and output document content use ``base64data``.
    """
    result = redact_document(request.document, request.targets, request.options)
    if isinstance(result, RedactionError):
        raise ValueError(result.message)
    return RedactionResponse.from_document(result)


@redaction_mcp.tool()
def replace(request: ReplacementRequest) -> ReplacementResponse:
    """Permanently replace selected PDF text with safe dummy text."""
    result = replace_document(request.document, request.targets)
    if isinstance(result, ReplacementError):
        raise ValueError(result.message)
    return ReplacementResponse.from_document(result)


def create_mcp_app():
    """Return a streamable HTTP MCP ASGI application for mounting or serving."""
    return redaction_mcp.http_app(path="/mcp")
