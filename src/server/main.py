"""FastAPI application exposing the document redaction service."""

import uvicorn

from fastapi import FastAPI, HTTPException

from src.core.models.errors import RedactionError
from src.core.services.redaction_service import redact_document
from src.server.models import RedactionRequest, RedactionResponse

app = FastAPI(title="redaction-mcp-fastapi", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/redact", response_model=RedactionResponse)
async def redact(request: RedactionRequest) -> RedactionResponse:
    result = redact_document(request.document, request.targets, request.options)
    if isinstance(result, RedactionError):
        raise HTTPException(status_code=422, detail=result.message)
    return RedactionResponse.from_document(result)


def main() -> None:
    """Entry point for the ``redaction-fastapi`` command."""
    uvicorn.run("src.server.main:app", host="0.0.0.0", port=8080, reload=True)
