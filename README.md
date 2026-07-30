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
- [x] Implementation of Core data replecement service
- [ ] Test the redaction and data replacement service for inconsistencies
- [ ] Improve redaction on edge cases - Multi-line redactions, optimizations on findings, etc.
- [ ] Improve data replacement on edge cases - Inline replacement, multi-line replacement, optmization of implementation, etc.


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
| `POST` | `/redact` | Redact a supported document and return the result as base64. |
| `POST` | `/replace` | Replace selected text and return the result as base64. |

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

The server provides three tools:

- `inspect_document` extracts bounded, page/slide-grouped text so an agent can
  identify exact text or regex targets.
- `redact` permanently redacts text, regex, page, or bounding-box targets.
- `replace` permanently replaces selected text.

MCP tools use flat arguments, for example:

```json
{
  "document": {"base64data": "...", "filename": "contract.pdf"},
  "targets": [{"type": "text", "values": ["Confidential"]}]
}
```

For MCP, `document` accepts only `base64data`, `filename`, and `mime_type`.
This works the same in STDIO and HTTP mode and prevents server-side file reads
or URL fetches from agent-supplied input.

### MCP over HTTP

Start the streamable HTTP MCP server:

```bash
uv run redaction-mcp-http
```

It listens on `127.0.0.1:8000` by default and exposes MCP at `/mcp`. Configure
the bind address or port with `MCP_HOST` and `MCP_PORT`. A non-loopback bind
also requires `MCP_AUTH_TOKEN`; clients must send it as a bearer token:

```bash
MCP_HOST=0.0.0.0 MCP_PORT=8000 MCP_AUTH_TOKEN='replace-with-a-secret' uv run redaction-mcp-http
```

## FastAPI document input contract

Every request has `document`, `targets`, and optional `options` fields.
Exactly one document source must be provided:

| Field | Use case | Notes |
| --- | --- | --- |
| `path` | Local server-side file | The path is resolved on the API/MCP server, not the client machine. |
| `url` | Remote file | The server downloads the URL before processing. |
| `base64data` | JSON/MCP payload | Standard base64-encoded document content. This is the recommended HTTP input. |

`data` is not accepted. `base64data` makes the binary encoding explicit and
keeps the API safe to serialize in JSON.

Supported presentation formats are `.pptx` and macro-enabled `.pptm`. Legacy
binary `.ppt` and slideshow `.ppsx` files are not supported by the PowerPoint
engine.

Plain-text `.txt` files are also supported. They have one logical page (index
`0`): page redaction masks every non-whitespace character while preserving the
text layout, and bounding-box redaction is unavailable.

Optional document metadata:

```json
{
  "filename": "contract.pdf",
  "mime_type": "application/pdf"
}
```

The response contains `filename`, `mime_type`, and `base64data`. Decode the
last field to retrieve the processed document.

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
  "pages": [0, 2],
  "ignore_case": true,
  "partial_match": false
}
```

`ignore_case` defaults to `false`. When enabled, each text value matches any
letter casing. `partial_match` defaults to `false`, so text is matched only
when it is not adjacent to an alphanumeric character. Set it to `true` to also
match text embedded within a larger alphanumeric value.

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

### Bounding boxes

```json
{
  "type": "bounding_box",
  "values": [{
    "page": 0,
    "x": 0.1,
    "y": 0.2,
    "width": 0.3,
    "height": 0.05
  }]
}
```

Bounding boxes always use normalized coordinates: `x`, `y`, `width`, and
`height` are proportions of page width and height and must remain within 0–1.
For DOCX, which does not retain rendered word positions, bounding-box matching
uses its document-order text layout and supports page index `0`.

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
  "permanent_redaction": true
}
```

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
```

Transport layers share `RedactionRequest` and `RedactionResponse`; the core
returns either a redacted `Document` or a typed `RedactionError`.

## Security notes

- Treat permanent redaction as irreversible. Keep source documents and verify
  output before distributing it.
- The FastAPI API accepts `path` and `url`, so expose it only to trusted
  callers or add source-access controls before deploying it publicly. MCP does
  not accept either source type.
- Keep the HTTP MCP endpoint on loopback unless `MCP_AUTH_TOKEN` is configured.
- Base64 increases payload size by roughly one third; use reasonable request
  limits and a file-storage workflow for large documents.

## Questions?
- If you have any questions or need any improvements/bug fixes, please raise a new GitHub issue stating the context.
- I'll try to follow up as soon as I can, but it may still be not very fast for some people. Please gimme time, `me slow` :)

---
### Happy Coding!!!
