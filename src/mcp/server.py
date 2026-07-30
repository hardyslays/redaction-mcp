"""Agent-focused FastMCP server over STDIO and streamable HTTP."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from src.core.models.document import Document
from src.core.models.errors import RedactionError, ReplacementError
from src.core.models.redaction import RedactionOptions, RedactionTarget
from src.core.models.replacement import ReplacementTarget
from src.core.services.redaction_service import redact_document
from src.core.services.replacement_service import replace_document
from starlette.middleware import Middleware

from src.mcp.auth import BearerTokenMiddleware
from src.mcp.inspection import DocumentInspection, inspect_document as inspect_mcp_document
from src.server.models import RedactionResponse, ReplacementResponse

try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - dependency installation error
    raise RuntimeError("Install the 'fastmcp' dependency to run the MCP server") from exc


redaction_mcp = FastMCP("redaction-mcp")


@redaction_mcp.tool()
async def redact(
    document: Document,
    targets: list[RedactionTarget],
    options: RedactionOptions = RedactionOptions(),
) -> RedactionResponse:
    """Permanently redact matching content in PDF, DOCX, PPTX/PPTM, or TXT.

    Call inspect_document first when targets are not already known. Page numbers
    are zero-based; all input and output document bytes use base64data.
    """
    result = redact_document(document, targets, options)
    if isinstance(result, RedactionError):
        raise ValueError(result.message)
    return RedactionResponse.from_document(result)


@redaction_mcp.tool()
async def replace(document: Document, targets: list[ReplacementTarget]) -> ReplacementResponse:
    """Permanently replace matching text in PDF, DOCX, PPTX/PPTM, or TXT."""
    result = replace_document(document, targets)
    if isinstance(result, ReplacementError):
        raise ValueError(result.message)
    return ReplacementResponse.from_document(result)


@redaction_mcp.tool()
async def inspect_document(
    document: Document,
    max_characters: Annotated[int, Field(ge=1, le=100_000)] = 20_000,
) -> DocumentInspection:
    """Extract bounded visible text so an agent can choose accurate targets.

    The result is grouped by zero-based page or slide. If ``truncated`` is true,
    call again with a smaller document or a larger allowed character budget.
    """
    return inspect_mcp_document(document, max_characters)


def create_mcp_app(auth_token: str | None = None):
    """Return the streamable HTTP MCP ASGI application at ``/mcp``.

    Callers exposing the server beyond a loopback interface must provide a
    bearer token; the command entry point enforces that deployment rule.
    """
    middleware = [Middleware(BearerTokenMiddleware, token=auth_token)] if auth_token else None
    return redaction_mcp.http_app(path="/mcp", middleware=middleware)
