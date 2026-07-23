# Redaction-MCP

Redaction-MCP is a document redaction service with a single core implementation and
three ways to expose it:

- a FastAPI JSON API;
- an MCP server over STDIO, for local MCP clients; and
- an MCP server over streamable HTTP.


## Scope and status

This will be aimed to be made into a full-fledged document Sensitive Data Protection MCP suite focused on inspection, de-identification and de-sensitization of documents. 
It will support various document formats such as PDF, PPT, DOCX, TIFF, etc. The scope of this MCP server is to provide SDP functionalities to the Ai agents and Agentic workflows.

Here is a rough roadmap of the project:

- [x] Initial Project structure setup
- [x] PDF doc redaction engine implementation
- [x] Implementation of Core Redaction service
- [x] Exposure of the services as a Fast API server (for testing)
- [x] MCP server implementation in STDIO mode
- [x] MCP server implementation in HTTP mode
- [x] DOCX doc redaction engine implementation
- [x] PPT doc redaction engine implementation
- [ ] TIFF doc redaction engine implementation
- [ ] Implementation of Core dat replecement service


## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) (recommended) or a Python environment with
  the dependencies installed

Install the application and development dependencies:

```bash
uv sync --extra dev
```

Run the test suite:

```bash
uv run python -m pytest --cov=src --cov-report=html
```

## Running the services

### FastAPI

Start the HTTP API on port 8080:

```bash
uv run python -m uvicorn src.server.main:app --port 8080
```

Once running, interactive OpenAPI documentation is available at
`http://127.0.0.1:8080/docs`.

Endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check, returning `{"status":"ok"}`. |
| `POST` | `/redact` | Redact a PDF, DOCX, or PPTX file and return the result as base64. |

### MCP over STDIO

Use this when an MCP client launches Redaction-MCP as a local subprocess:

```bash
uv run redaction-mcp-stdio
```

For example, an MCP client command can use:

```json
{
  "command": "uv",
  "args": ["run", "--directory", "/absolute/path/to/redaction", "redaction-mcp-stdio"]
}
```

The server provides one tool, `redact`, with the same request and response
schema as the FastAPI API.

### MCP over HTTP

Start the streamable HTTP MCP server:

```bash
uv run redaction-mcp-http
```

It listens on `127.0.0.1:8000` by default and exposes MCP at `/mcp`. Configure
the bind address or port with `MCP_HOST` and `MCP_PORT`:

```bash
MCP_HOST=0.0.0.0 MCP_PORT=8000 uv run redaction-mcp-http
```

## Document input contract

Every request has `document`, `targets`, and optional `options` fields.
Exactly one document source must be provided:

| Field | Use case | Notes |
| --- | --- | --- |
| `path` | Local server-side file | The path is resolved on the API/MCP server, not the client machine. |
| `url` | Remote file | The server downloads the URL before processing. |
| `base64data` | JSON/MCP payload | Standard base64-encoded document content. This is the recommended HTTP input. |

`data` is not accepted. `base64data` makes the binary encoding explicit and
keeps the API safe to serialize in JSON.

Optional document metadata:

```json
{
  "filename": "contract.pdf",
  "mime_type": "application/pdf"
}
```

Supported MIME types are `application/pdf`,
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`, and
`application/vnd.openxmlformats-officedocument.presentationml.presentation`.
The response contains `filename`, `mime_type`, and `base64data`. Decode the
last field to retrieve the redacted file.

## FastAPI examples

### Redact base64-encoded PDF content

On Linux, encode the file without line wrapping and submit it:

```bash
PDF_BASE64=$(base64 -w0 input.pdf)

curl --request POST http://127.0.0.1:8080/redact \
  --header 'Content-Type: application/json' \
  --data "{
    \"document\": {
      \"base64data\": \"${PDF_BASE64}\",
      \"filename\": \"input.pdf\",
      \"mime_type\": \"application/pdf\"
    },
    \"targets\": [{
      \"type\": \"text\",
      \"values\": [\"Confidential\"]
    }]
  }" > response.json

jq -r '.base64data' response.json | base64 --decode > redacted.pdf
```

### Redact a PDF available to the server by path

```bash
curl --request POST http://127.0.0.1:8080/redact \
  --header 'Content-Type: application/json' \
  --data '{
    "document": {"path": "/srv/documents/input.pdf"},
    "targets": [{"type": "text", "values": ["Confidential"]}]
  }'
```

Do not use a client-local path with a remotely hosted API; use `base64data` or
a URL instead.

## Redaction targets


### Text

Redact all exact occurrences, optionally restricted to pages:

```json
{
  "type": "text",
  "values": ["Jane Doe", "123-45-6789"],
  "pages": [0, 2]
}
```

### Regular expression

```json
{
  "type": "regex",
  "patterns": ["[A-Z]{2}\\d{6}"],
  "ignore_case": false,
  "only_first_match": false,
  "allow_unicode": false
}
```

Regular expressions are evaluated against extracted words, so each match
redacts the matching word's rectangle. Cross-word or multi-line expressions
are not currently supported.

For DOCX, text and regex redactions replace matching text in paragraphs,
tables, headers, and footers. DOCX does not support page-restricted or
bounding-box targets because pagination is determined by the document renderer.
For PPTX, text and regex redactions apply to slide text frames; page and
bounding-box targets add a cover shape on the selected slide area.

### Bounding boxes

```json
{
  "type": "bounding_box",
  "values": [{
    "page": 0,
    "x": 0.1,
    "y": 0.2,
    "width": 0.3,
    "height": 0.05,
    "units": "normalized"
  }]
}
```

`normalized` coordinates are proportions of page width and height. `inches`
are converted to PDF points (72 points per inch); `pixels` are used directly
as PDF points.

### Pages

For a whole page, use:

```json
{"type": "page", "values": [0, 3]}
```

## Options

```json
{
  "fill_color": "#000000",
  "fill_opacity": 1.0,
  "permanent_redaction": true,
  "redaction_type": "asterisks"
}
```

For DOCX and PPTX text redaction, `redaction_type` controls the replacement:
`asterisks` (the default) replaces each character with `*`, while `mask`
replaces each match with `[REDACT]`. PDF redactions continue to use their
existing black-box behavior.
`fill_color` must be a six-digit hexadecimal color. With
`permanent_redaction: true` (the default), redaction annotations are applied,
removing content in the redacted areas from the output PDF. Set it to `false`
only when a reviewable, annotation-based result is needed.

## Architecture

```text
FastAPI / MCP STDIO / MCP HTTP
              |
              v
    src.core.services.redaction_service
              |
              v
    src.core.engines.pdf_redaction (PyMuPDF)
    src.core.engines.docx_redaction (python-docx)
    src.core.engines.pptx_redaction (python-pptx)
```

Transport layers share `RedactionRequest` and `RedactionResponse`; the core
returns either a redacted `Document` or a typed `RedactionError`.

## Security notes

- Treat permanent redaction as irreversible. Keep source documents and verify
  output before distributing it.
- A `path` or `url` makes the server read that resource. Expose this service
  only to trusted callers, or add authentication and source-access controls
  before deploying it publicly.
- Base64 increases payload size by roughly one third; use reasonable request
  limits and a file-storage workflow for large documents.

## Questions?
- If you have any questions or need any improvements/bug fixes, please raise a new GitHub issue stating the context.
- I'll try to follow up as soon as I can, but it may still be not very fast for some people. Please gimme time, `me slow` :)

---
### Happy Coding!!!
