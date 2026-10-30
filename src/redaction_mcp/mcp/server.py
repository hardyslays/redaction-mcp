"""Agent-focused FastMCP server over STDIO and streamable HTTP."""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from pydantic import Field

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.errors import RedactionError, ReplacementError
from redaction_mcp.core.models.jobs import DeleteResult, JobInfo, JobSubmit, ResultChunk, UploadCreate, UploadInfo
from redaction_mcp.core.models.redaction import RedactionOptions, RedactionTarget
from redaction_mcp.core.models.replacement import ReplacementTarget
from redaction_mcp.core.services.redaction_service import redact_document
from redaction_mcp.core.services.replacement_service import replace_document
from starlette.middleware import Middleware

from redaction_mcp.mcp.auth import BearerTokenMiddleware
from redaction_mcp.mcp.inspection import DocumentInspection, inspect_document as inspect_mcp_document
from redaction_mcp.core.services.jobs import get_job_manager
from redaction_mcp.core.services.limits import run_direct, run_io
from redaction_mcp.server.models import RedactionResponse, ReplacementResponse

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
    """Permanently redact matching content in PDF, DOCX, PPTX/PPTM, TXT, CSV, or XLSX.

    Call inspect_document first when targets are not already known. Page numbers
    are zero-based; all input and output document bytes use base64data.
    """
    result = await run_direct(redact_document, document, targets, options)
    if isinstance(result, RedactionError):
        raise ValueError(result.message)
    return RedactionResponse.from_document(result)


@redaction_mcp.tool()
async def replace(document: Document, targets: list[ReplacementTarget]) -> ReplacementResponse:
    """Permanently replace matching text in PDF, DOCX, PPTX/PPTM, TXT, CSV, or XLSX."""
    result = await run_direct(replace_document, document, targets)
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
    return await run_direct(inspect_mcp_document, document, max_characters)


@redaction_mcp.tool()
async def create_upload(filename: str, size_bytes: int, mime_type: str | None = None) -> UploadInfo:
    """Create a durable upload for a large document and return its upload ID."""
    return await run_io(
        lambda: get_job_manager().create_upload(UploadCreate(filename=filename, mime_type=mime_type, size_bytes=size_bytes))
    )


@redaction_mcp.tool()
async def upload_chunk(upload_id: str, index: int, base64data: str) -> UploadInfo:
    """Append one sequential base64 chunk of at most 1 MiB to an upload."""
    try:
        data = base64.b64decode(base64data, validate=True)
    except ValueError as exc:
        raise ValueError("base64data must be valid base64") from exc
    return await run_io(lambda: get_job_manager().upload_chunk(upload_id, index, data))


@redaction_mcp.tool()
async def submit_job(
    upload_id: str,
    operation: Literal["redact", "replace"],
    targets: list[dict[str, object]],
    options: RedactionOptions | None = None,
) -> JobInfo:
    """Queue a completed upload for durable redaction or replacement."""
    return await run_io(
        lambda: get_job_manager().submit_job(
            JobSubmit(upload_id=upload_id, operation=operation, targets=targets, options=options)
        )
    )


@redaction_mcp.tool()
async def get_job(job_id: str) -> JobInfo:
    """Return durable job state and result metadata."""
    return await run_io(lambda: get_job_manager().get_job(job_id))


@redaction_mcp.tool()
async def get_result_chunk(job_id: str, offset: int = 0, limit: int = 1024 * 1024) -> ResultChunk:
    """Return up to 1 MiB of a completed result as base64."""
    return await run_io(lambda: get_job_manager().result_chunk(job_id, offset, limit))


@redaction_mcp.tool()
async def delete_job(job_id: str) -> DeleteResult:
    """Delete a queued or terminal job and its unreferenced files."""
    await run_io(lambda: get_job_manager().delete_job(job_id))
    return DeleteResult()


def create_mcp_app(auth_token: str | None = None):
    """Return the streamable HTTP MCP ASGI application at ``/mcp``.

    Callers exposing the server beyond a loopback interface must provide a
    bearer token; the command entry point enforces that deployment rule.
    """
    middleware = [Middleware(BearerTokenMiddleware, token=auth_token)] if auth_token else None
    return redaction_mcp.http_app(path="/mcp", middleware=middleware)
