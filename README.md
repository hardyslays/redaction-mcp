# Redaction-MCP

Redaction-MCP provides document redaction, synthetic data replacement, and
bounded text inspection through FastAPI and MCP. It supports PDF, DOCX,
PPTX/PPTM, TXT, CSV, and XLSX documents.

## Install

Python 3.12 or newer is required.

```bash
python -m pip install redaction-mcp
```

For optional English name and place inference, install the NLP extra and its
small pipeline. The server never downloads a model at runtime.

```bash
python -m pip install "redaction-mcp[nlp]"
python -m spacy download en_core_web_sm
```

For a source checkout, either run `uv sync --extra dev` or install
`requirements.txt` followed by `python -m pip install -e .`.

## Run

The installed console commands work from any directory:

```bash
redaction-mcp-stdio
redaction-mcp-http
redaction-fastapi
```

Use `MCP_HOST`, `MCP_PORT`, and `MCP_AUTH_TOKEN` to configure MCP HTTP. Use
`FASTAPI_HOST`, `FASTAPI_PORT`, and `FASTAPI_AUTH_TOKEN` for FastAPI. A bearer
token is required when either HTTP service binds to a non-loopback address.

## Direct processing

FastAPI exposes `POST /redact` and `POST /replace`. MCP exposes `redact`,
`replace`, and `inspect_document`. Direct requests carry document content in
`base64data` and are limited to 16 MiB decoded.

Literal matching applies Unicode NFKC normalization, collapses whitespace,
deduplicates findings, prefers longer findings, and uses Unicode-aware word
boundaries. Set `partial_match` to allow a finding inside a larger word.

Replacement supports `PARTIAL`, `STATIC`, `REGEX`, and `SEMANTIC`. Semantic
targets may provide `entity_type`, including `NAME`, `EMAIL`, `PHONE`, `SSN`,
`CREDIT_CARD`, `ADDRESS`, `ZIPCODE`, `DATE`, and `PLACE`. Structured types are
inferred without a model; the optional spaCy pipeline improves English name
and place inference.

## Large-file jobs

FastAPI provides this durable workflow:

1. `POST /uploads` with `filename`, optional `mime_type`, and `size_bytes`.
2. `PUT /uploads/{id}/chunks/{index}` with sequential raw chunks up to 1 MiB.
3. `POST /jobs` with `upload_id`, `operation`, `targets`, and optional redaction
   `options`.
4. Poll `GET /jobs/{id}` and download binary ranges from
   `GET /jobs/{id}/result?offset=0&limit=1048576`.
5. `DELETE /jobs/{id}` when the result is no longer needed.

MCP provides equivalent `create_upload`, `upload_chunk`, `submit_job`,
`get_job`, `get_result_chunk`, and `delete_job` tools. MCP chunks use base64.

Jobs and files are stored in `~/.redaction-mcp` by default. Set
`REDACTION_STORAGE_PATH`, `REDACTION_JOB_WORKERS`, and
`REDACTION_JOB_RETENTION_SECONDS` to configure storage, concurrency, and
retention. PDF and CSV uploads are limited to 512 MiB. DOCX, PPTX/PPTM, and
XLSX uploads are limited to 32 MiB and checked for unsafe archive expansion.
TXT and unrecognized formats retain the 16 MiB direct-processing limit.

## Development

```bash
uv sync --extra dev
uv run python -m pytest --cov=src --cov-report=html
uv build
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for request schemas and implementation
details.

```bash
git tag v0.2.0
git push origin v0.2.0
```

The tag must point to a commit that already contains this workflow. A version
that has already been uploaded to PyPI cannot be uploaded again.
