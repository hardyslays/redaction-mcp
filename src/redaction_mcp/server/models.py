"""HTTP/MCP-safe request and response models."""

from pydantic import BaseModel, Field

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.redaction import RedactionOptions, RedactionTarget
from redaction_mcp.core.models.replacement import ReplacementTarget


class RedactionRequest(BaseModel):
    document: Document
    targets: list[RedactionTarget] = Field(min_length=1)
    options: RedactionOptions = Field(default_factory=RedactionOptions)

class RedactionResponse(BaseModel):
    filename: str
    mime_type: str
    base64data: str

    @classmethod
    def from_document(cls, document: Document) -> "RedactionResponse":
        return cls(
            filename=document.filename or "redacted.pdf",
            mime_type=document.mime_type or "application/octet-stream",
            base64data=document.base64data,
        )


class ReplacementRequest(BaseModel):
    document: Document
    targets: list[ReplacementTarget] = Field(min_length=1)


class ReplacementResponse(RedactionResponse):
    @classmethod
    def from_document(cls, document: Document) -> "ReplacementResponse":
        return cls(
            filename=document.filename or "replaced.pdf",
            mime_type=document.mime_type or "application/octet-stream",
            base64data=document.base64data,
        )
