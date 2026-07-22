"""FastAPI application exposing the document redaction service."""

from fastapi import FastAPI, HTTPException

from src.core.models.errors import RedactionError
from src.core.services.redaction_service import redact_document
from src.server.models import RedactionRequest, RedactionResponse

app = FastAPI(title="PyRedaction", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/redact", response_model=RedactionResponse)
async def redact(request: RedactionRequest) -> RedactionResponse:
    result = redact_document(request.document, request.targets, request.options)
    if isinstance(result, RedactionError):
        raise HTTPException(status_code=422, detail=result.message)
    return RedactionResponse.from_document(result)
