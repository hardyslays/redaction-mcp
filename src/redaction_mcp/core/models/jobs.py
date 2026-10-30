"""Models for durable, chunked document-processing jobs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from redaction_mcp.core.models.redaction import RedactionOptions


class UploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    mime_type: str | None = None
    size_bytes: int = Field(ge=1)


class UploadInfo(BaseModel):
    upload_id: str
    filename: str
    mime_type: str | None
    size_bytes: int
    received_bytes: int
    next_chunk: int


class JobSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    operation: Literal["redact", "replace"]
    targets: list[dict[str, object]] = Field(min_length=1)
    options: RedactionOptions | None = None


class JobInfo(BaseModel):
    job_id: str
    upload_id: str
    operation: Literal["redact", "replace"]
    status: Literal["queued", "running", "succeeded", "failed"]
    filename: str | None = None
    mime_type: str | None = None
    output_size: int | None = None
    error: str | None = None
    created_at: float
    updated_at: float


class ResultChunk(BaseModel):
    base64data: str
    offset: int
    next_offset: int
    total_size: int
    eof: bool


class DeleteResult(BaseModel):
    deleted: bool = True
