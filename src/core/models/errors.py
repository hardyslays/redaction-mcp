from typing import Optional, Final
from pydantic import BaseModel

class GenericErrorModel(BaseModel):
    status: Final[str] = "error"
    type: str
    message: str
    trace: Optional[str] = None

class RedactionError(GenericErrorModel):
    """
    Represents error model for Redaction error
    """
    type: Final[str] = "RedactionError" # type: ignore[assignment]