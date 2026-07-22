from typing import Literal
from pydantic import BaseModel

class GenericErrorModel(BaseModel):
    status: Literal["error"] = "error"
    type: str
    message: str
    trace: str | None = None

class RedactionError(GenericErrorModel):
    """
    Represents error model for Redaction error
    """
    type: Literal["RedactionError"] = "RedactionError"
