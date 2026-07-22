import base64
from pydantic import BaseModel, ConfigDict, model_validator
from enum import Enum

# Define the source types possible for document
class SourceType(str, Enum):
    PATH = "path"
    URL = "url"
    BASE64DATA = "base64data"

# Define Document source model that will contain the type and value of the source
class DocumentSource(BaseModel):
    type: SourceType
    value: str

# Define Document model that can contain either a path, URL, or bytes data
class Document(BaseModel):
    """
    Represents a document model for input and output.

    ``base64data`` is used for in-memory documents so the model remains safe to
    serialize across JSON-based HTTP and MCP transports.
    """
    model_config = ConfigDict(extra="forbid")

    path: str | None = None          # Local STDIO use
    url: str | None = None           # HTTP or cloud storage
    base64data: str | None = None    # Small in-memory documents

    mime_type: str | None = None
    filename: str | None = None

    @model_validator(mode="after")
    def validate_single_source(self):
        provided_sources = sum(x is not None for x in [self.path, self.url, self.base64data])
        if provided_sources != 1:
            raise ValueError("Exactly one of path, url, or base64data must be provided")
        if self.base64data is not None:
            try:
                base64.b64decode(self.base64data, validate=True)
            except ValueError as exc:
                raise ValueError("base64data must be valid base64") from exc
        return self

    def get_source(self) -> DocumentSource:
        if self.path is not None:
            return DocumentSource(type=SourceType.PATH, value=self.path)
        elif self.url is not None:
            return DocumentSource(type=SourceType.URL, value=self.url)
        elif self.base64data is not None:
            return DocumentSource(type=SourceType.BASE64DATA, value=self.base64data)
        else:
            raise ValueError("No valid source found in the document")

    def decoded_bytes(self) -> bytes:
        """Decode the in-memory base64 payload."""
        if self.base64data is None:
            raise ValueError("Document does not contain base64data")
        return base64.b64decode(self.base64data, validate=True)
