# Development guide

This guide describes the implemented architecture and public contracts of
Redaction-MCP. It is intended for contributors extending document support,
matching behavior, or transports.

## Purpose and supported formats

The project provides permanent document redaction and text replacement through
one transport-independent core. The current supported input formats are PDF,
DOCX, PPTX, PPTM, and UTF-8 TXT. Every successful operation returns the output
as base64-encoded content.

| Operation | PDF | DOCX | PPTX/PPTM | TXT |
| --- | --- | --- | --- | --- |
| Redact literal text | Yes | Yes | Yes | Yes |
| Redact regular expressions | Yes | Yes | Yes | Yes |
| Redact normalized bounding boxes | Yes | Approximate layout | Approximate layout | No |
| Redact a full page/slide | Yes | No | Yes | Logical page `0` |
| Replace literal text | Yes | Yes | Yes | Yes |

PPTM files are accepted as input but are saved by `python-pptx` as a PPTX
output. Legacy `.ppt`, `.ppsx`, and non-UTF-8 text files are not supported.

## Setup and verification

The project requires Python 3.12 or newer. `uv` is the recommended environment
manager.

```bash
uv sync --extra dev
uv run python -m pytest --cov=src --cov-report=html
```

The tests are organized by transport and service behavior:

- `tests/test_fastapi.py` verifies the HTTP API.
- `tests/test_mcp.py` verifies MCP STDIO and HTTP protocol behavior.
- `tests/test_redaction_service.py` verifies matching, redaction engines, and
  document handling.
- `tests/test_replacement_service.py` verifies replacement engines and output
  behavior.

Add focused, independent tests whenever a core model, engine, or transport
contract changes.

## Project layout

```text
src/
├── cli/
│   ├── main.py                    # MCP STDIO entry point
│   └── stdio.py                   # POSIX-safe MCP STDIO transport
├── mcp/
│   ├── auth.py                    # Optional HTTP bearer-token middleware
│   ├── inspection.py              # Bounded document-text extraction tool
│   ├── main.py                    # Streamable HTTP MCP entry point
│   └── server.py                  # FastMCP tools and ASGI application factory
├── server/
│   ├── main.py                    # FastAPI application and routes
│   └── models.py                  # FastAPI request and shared response models
└── core/
    ├── models/                    # Pydantic input, target, option, and error models
    ├── services/                  # MIME routing, matching, and replacement rules
    └── engines/                   # Format-specific redaction and replacement implementations
tests/                             # Unit and transport tests
```

## Architecture

FastAPI and MCP use the same base64-only `Document` model and the same core
services. Transport code validates the request, calls a service, and translates
a typed core error into its protocol's error behavior. No transport resolves
agent-provided filesystem paths or URLs.

```text
FastAPI (/redact, /replace) ─┐
MCP STDIO (inspect, redact, replace) ─┼─> base64 Document → core services
MCP HTTP  (/mcp)                    ─┘                          |
                                              v
                         redaction_service / replacement_service
                                              |
                     decode bytes → detect MIME → select format engine
                                              |
       PDF (PyMuPDF) | DOCX (python-docx) | PPTX (python-pptx) | TXT
                                              |
                                  output Document (base64data)
```

`redact_document` returns either `Document` or `RedactionError`;
`replace_document` returns either `Document` or `ReplacementError`. FastAPI
maps these typed errors to HTTP 422, while MCP raises `ValueError` with the
message.

### Request and response models

`src/server/models.py` owns the FastAPI request models and the response models
returned by both transports:

```python
class RedactionRequest(BaseModel):
    document: Document
    targets: list[RedactionTarget]  # at least one
    options: RedactionOptions       # defaults to RedactionOptions()

class ReplacementRequest(BaseModel):
    document: Document
    targets: list[TextReplacementTarget]  # at least one
```

Both response types contain `filename`, `mime_type`, and `base64data`.
Responses contain in-memory base64 output only.

## Core document contract

`Document` has `extra="forbid"` and requires base64 document data:

| Field | Meaning |
| --- | --- |
| `base64data` | Valid standard base64 document content used by every transport. |

Optional `filename` and `mime_type` guide type detection and output naming.
When `mime_type` is absent, the service detects PDF signatures, Office package
metadata, then a known file extension. The recognized MIME types are
`application/pdf`, DOCX, PPTX, PPTM, and `text/plain`.

Base64 increases payload size by roughly one-third, so configure request-size
limits for the deployment.

## Redaction contract

Every redaction request contains one or more discriminated targets. Page and
slide numbers are zero-based.

### Literal text target

```json
{
  "type": "text",
  "values": ["Jane Doe", "123-45-6789"],
  "pages": [0, 2],
  "ignore_case": false,
  "partial_match": false
}
```

Literal values are escaped before matching. By default they must not be
adjacent to ASCII letters or digits; set `partial_match` to `true` to permit
matches inside a larger alphanumeric value. `ignore_case` defaults to `false`.

### Regular-expression target

```json
{
  "type": "regex",
  "patterns": ["[A-Z]{2}\\d{6}"],
  "pages": [0],
  "ignore_case": true,
  "only_first_match": false,
  "allow_unicode": false
}
```

Regex targets compile each supplied expression. `ignore_case` defaults to
`true`; with `allow_unicode: false` the expression uses ASCII regular-expression
semantics. `only_first_match` stops after the first match for that target (PDF
and PPTX search document-wide; TXT stops after the first matching pattern).

### Bounding-box target

```json
{
  "type": "bounding_box",
  "values": [{"page": 0, "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05}]
}
```

Coordinates are normalized proportions of the target page or slide. `x`, `y`,
`width`, and `height` must be within 0–1, widths and heights must be positive,
and each rectangle must fit within its page.

PDF uses rendered page coordinates. DOCX and PPTX estimate word locations from
document/text-frame layout, so they are useful for coarse selections but not
pixel-accurate redaction. DOCX accepts only logical page `0`; TXT rejects
bounding boxes.

### Page target

```json
{"type": "page", "values": [0, 3]}
```

For PDFs, this applies a redaction annotation over each full page. For PPTX it
removes every shape on the selected slide. DOCX does not support page targets.
TXT has one logical page (`0`) and replaces every non-whitespace character with
an asterisk.

### Options

```json
{
  "fill_color": "#000000",
  "fill_opacity": 1.0,
  "permanent_redaction": true,
  "redaction_type": "asterisks"
}
```

`fill_color` is validated as a six-digit hexadecimal color.
`fill_opacity` is between 0 and 1. `permanent_redaction` defaults to `true` and
applies PDF annotations; setting it to `false` keeps reviewable PDF
annotations. `redaction_type` is `asterisks` by default or `mask` (`[REDACT]`)
for DOCX/PPTX text replacements. TXT always uses asterisks and ignores these
options.

## Replacement contract

Replacement targets currently support literal text only:

```json
{
  "type": "text",
  "values": ["Jane Doe"],
  "replacement_type": "PARTIAL",
  "static_text": "REDACTED",
  "pages": [0],
  "ignore_case": false,
  "partial_match": false
}
```

`replacement_type` is required:

| Type | Result |
| --- | --- |
| `PARTIAL` | Masks non-whitespace characters while retaining the last 2 or 4 visible characters. |
| `STATIC` | Inserts `[static_text]`; `static_text` is required only for this type. |
| `REGEX` | Generates deterministic dummy text by rotating letters and digits one position. |

Replacement uses the same literal matching rules as a redaction text target.
PDF replacement removes the source text with redactions, then inserts the
replacement close to the original baseline with a best-effort font, color, and
background match. DOCX and PPTX rebuild matching paragraph/text-frame content,
which can discard run-level formatting in the changed content.

Page filters work for PDF and PPTX. DOCX replacement rejects page filters; TXT
accepts only page `0`.

## Format-specific implementation notes

- PDF engines use PyMuPDF character boxes. They support multiline literal and
  regex matches and permanently remove source content when annotations are
  applied.
- DOCX engines visit body paragraphs, tables (including nested tables), headers,
  and footers. Page geometry is not part of a DOCX document model, hence the
  approximate bounding-box behavior and lack of page filters.
- PPTX engines process text boxes, table cells, and grouped shapes. Full-slide
  redaction removes shapes rather than drawing an overlay.
- TXT engines decode and encode UTF-8, preserving whitespace and line layout.

When adding another document type, implement both engines if the feature is
intended to be supported for redaction and replacement, add MIME detection,
route it in both services, and cover its success and unsupported-target cases.

## Running transports

```bash
# FastAPI: http://127.0.0.1:8080/docs
uv run redaction-fastapi

# MCP launched as a local subprocess
uv run redaction-mcp-stdio

# Streamable HTTP MCP: http://127.0.0.1:8000/mcp
uv run redaction-mcp-http
```

Set `MCP_HOST` and `MCP_PORT` to configure the HTTP MCP bind address and port.
Set `MCP_AUTH_TOKEN` as well when binding outside loopback; MCP clients then
send it as a bearer token. The FastAPI application provides `GET /health`,
`POST /redact`, and `POST /replace`. MCP exposes corresponding `redact` and
`replace` tools, plus `inspect_document` for selecting targets.

## Contributor checklist

1. Keep business logic in `src/core`; do not duplicate it in FastAPI or MCP.
2. Preserve the public Pydantic models when changing a transport contract, or
   document and test an intentional breaking change.
3. Return `Document` or the appropriate typed core error from services.
4. Add atomic tests for engine behavior and for transport-visible changes.
5. Run the full test command before submitting a change.

Permanent redaction and replacement are irreversible operations. Always retain
the original document and inspect generated output before distribution.
