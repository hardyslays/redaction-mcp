"""FastAPI application exposing direct and durable document processing."""

import os
import uvicorn

from fastapi import FastAPI, HTTPException, Query, Request, Response

from redaction_mcp.core.models.errors import RedactionError, ReplacementError
from redaction_mcp.core.services.redaction_service import redact_document
from redaction_mcp.core.services.replacement_service import replace_document
from redaction_mcp.server.models import RedactionRequest, RedactionResponse, ReplacementRequest, ReplacementResponse
from redaction_mcp.core.models.jobs import DeleteResult, JobInfo, JobSubmit, UploadCreate, UploadInfo
from redaction_mcp.core.services.jobs import JobConflictError, JobNotFoundError, get_job_manager
from redaction_mcp.core.services.limits import CHUNK_SIZE, run_direct, run_io
from redaction_mcp.mcp.auth import BearerTokenMiddleware

app = FastAPI(title="redaction-mcp-fastapi", version="0.2.0")
if os.getenv("FASTAPI_AUTH_TOKEN"):
    app.add_middleware(BearerTokenMiddleware, token=os.environ["FASTAPI_AUTH_TOKEN"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/redact", response_model=RedactionResponse)
async def redact(request: RedactionRequest) -> RedactionResponse:
    result = await run_direct(redact_document, request.document, request.targets, request.options)
    if isinstance(result, RedactionError):
        raise HTTPException(status_code=422, detail=result.message)
    return RedactionResponse.from_document(result)


@app.post("/replace", response_model=ReplacementResponse)
async def replace(request: ReplacementRequest) -> ReplacementResponse:
    result = await run_direct(replace_document, request.document, request.targets)
    if isinstance(result, ReplacementError):
        raise HTTPException(status_code=422, detail=result.message)
    return ReplacementResponse.from_document(result)


def _job_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, JobNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, JobConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@app.post("/uploads", response_model=UploadInfo, status_code=201)
async def create_upload(request: UploadCreate) -> UploadInfo:
    try:
        return await run_io(lambda: get_job_manager().create_upload(request))
    except ValueError as exc:
        raise _job_error(exc) from exc


@app.put("/uploads/{upload_id}/chunks/{index}", response_model=UploadInfo)
async def upload_chunk(upload_id: str, index: int, request: Request) -> UploadInfo:
    chunk = await request.body()
    try:
        return await run_io(lambda: get_job_manager().upload_chunk(upload_id, index, chunk))
    except ValueError as exc:
        raise _job_error(exc) from exc


@app.post("/jobs", response_model=JobInfo, status_code=202)
async def submit_job(request: JobSubmit) -> JobInfo:
    try:
        return await run_io(lambda: get_job_manager().submit_job(request))
    except ValueError as exc:
        raise _job_error(exc) from exc


@app.get("/jobs/{job_id}", response_model=JobInfo)
async def get_job(job_id: str) -> JobInfo:
    try:
        return await run_io(lambda: get_job_manager().get_job(job_id))
    except ValueError as exc:
        raise _job_error(exc) from exc


@app.get("/jobs/{job_id}/result")
async def get_result(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=CHUNK_SIZE, ge=1, le=CHUNK_SIZE),
) -> Response:
    try:
        data, job = await run_io(lambda: get_job_manager().result_bytes(job_id, offset, limit))
    except ValueError as exc:
        raise _job_error(exc) from exc
    headers = {
        "Content-Disposition": f'attachment; filename="{job.filename or "result.bin"}"',
        "X-Result-Size": str(job.output_size),
        "X-Next-Offset": str(offset + len(data)),
    }
    return Response(data, media_type=job.mime_type or "application/octet-stream", headers=headers)


@app.delete("/jobs/{job_id}", response_model=DeleteResult)
async def delete_job(job_id: str) -> DeleteResult:
    try:
        await run_io(lambda: get_job_manager().delete_job(job_id))
    except ValueError as exc:
        raise _job_error(exc) from exc
    return DeleteResult()


def main() -> None:
    """Entry point for the ``redaction-fastapi`` command."""
    host = os.getenv("FASTAPI_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "::1", "localhost"} and not os.getenv("FASTAPI_AUTH_TOKEN"):
        raise RuntimeError("FASTAPI_AUTH_TOKEN is required when FASTAPI_HOST is not a loopback address")
    uvicorn.run("redaction_mcp.server.main:app", host=host, port=int(os.getenv("FASTAPI_PORT", "8080")))
