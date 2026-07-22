"""HTTP/MCP-safe request and response models."""

from pydantic import BaseModel, Field

from src.core.models.document import Document
from src.core.models.redaction import RedactionOptions, RedactionTarget


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
        if document.base64data is None:
            raise ValueError("Redaction output did not include base64data")
        return cls(
            filename=document.filename or "redacted.pdf",
            mime_type=document.mime_type or "application/octet-stream",
            base64data=document.base64data,
        )
