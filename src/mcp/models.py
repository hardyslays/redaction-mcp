"""Models intentionally exposed through the MCP tool interface."""

from __future__ import annotations

import base64

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.models.document import Document


class McpDocument(BaseModel):
    """A document supplied directly by an MCP client.

    MCP clients may be remote from the server, so server-local paths and URLs
    are deliberately not accepted here.  This prevents accidental file access
    and SSRF while giving every transport one portable input form.
    """

    model_config = ConfigDict(extra="forbid")

    base64data: str = Field(description="Standard base64-encoded document bytes.")
    mime_type: str | None = None
    filename: str | None = None

    @field_validator("base64data")
    @classmethod
    def valid_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("base64data must be valid base64") from exc
        return value

    def to_document(self) -> Document:
        return Document(
            base64data=self.base64data,
            mime_type=self.mime_type,
            filename=self.filename,
        )


class DocumentPage(BaseModel):
    """Text visible to an agent on one zero-based document page or slide."""

    index: int = Field(ge=0)
    text: str


class DocumentInspection(BaseModel):
    """Bounded text extraction that lets agents select redaction targets."""

    filename: str
    mime_type: str
    pages: list[DocumentPage]
    truncated: bool
