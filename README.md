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

## Publishing

The [PyPI workflow](.github/workflows/publish-to-pypi.yml) runs the tests,
builds and checks the distributions, then publishes them when a pull request
is merged into a `release-<major>.<minor>.<patch>` branch. The branch version
must match `pyproject.toml`. It uses PyPI Trusted Publishing, so no PyPI API
token is needed.

Before the first release, create a GitHub environment named `pypi` with a
deployment branch rule for `release-*`. In the existing
[redaction-mcp PyPI project](https://pypi.org/manage/project/redaction-mcp/publishing/),
add a GitHub Actions Trusted Publisher with owner `hardyslays`, repository
`redaction-mcp`, workflow filename `publish-to-pypi.yml`, and environment
`pypi`.

For each release, set a new, unused version in `pyproject.toml` and create a
matching branch from `main` (for example, `release-1.0.0` for version `1.0.0`):

```bash
git switch main
git pull --ff-only origin main
git switch -c release-1.0.0
git push -u origin release-1.0.0
```

The branch must already contain the workflow. Open a pull request targeting
that release branch and merge it when ready. Creating the branch alone does not
publish. Publish only once from each release branch; PyPI does not permit
uploading the same version again.
