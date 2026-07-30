"""In-memory document model shared by every transport."""

import base64

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator


class Document(BaseModel):
    """A base64-encoded document safe for JSON and MCP transports."""

    model_config = ConfigDict(extra="forbid")

    base64data: str
    mime_type: str | None = None
    filename: str | None = None
    _decoded_base64: bytes | None = PrivateAttr(default=None)

    @field_validator("base64data")
    @classmethod
    def valid_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("base64data must be valid base64") from exc
        return value

    def decoded_bytes(self) -> bytes:
        """Return the validated in-memory document bytes."""
        if self._decoded_base64 is None:
            self._decoded_base64 = base64.b64decode(self.base64data, validate=True)
        return self._decoded_base64
