from typing import Optional
from pydantic import BaseModel, model_validator
from enum import Enum

# Define the source types possible for document
class SourceType(str, Enum):
    PATH = "path"
    URL = "url"
    BYTES = "bytes"

# Define Document source model that will contain the type and value of the source
class DocumentSource(BaseModel):
    type: SourceType
    value: str | bytes

# Define Document model that can contain either a path, URL, or bytes data
class Document(BaseModel):
    """
    Represents a document model for input and output.
    """
    path: Optional[str] | None = None          # Local STDIO use
    url: Optional[str] | None = None           # HTTP or cloud storage
    data: Optional[bytes] | None = None        # Small in-memory documents

    mime_type: Optional[str] | None = None
    filename: Optional[str] | None = None

    @model_validator(mode="after")
    def validate_single_source(self):
        provided_sources = sum(x is not None for x in [self.path, self.url, self.data])
        if provided_sources != 1:
            raise ValueError("Exactly one of path, url, or data must be provided")
        return self

    def get_source(self) -> DocumentSource:
        if self.path is not None:
            return DocumentSource(type=SourceType.PATH, value=self.path)
        elif self.url is not None:
            return DocumentSource(type=SourceType.URL, value=self.url)
        elif self.data is not None:
            return DocumentSource(type=SourceType.BYTES, value=self.data)
        else:
            raise ValueError("No valid source found in the document")