# Redaction-MCP

## Introduction
Redaction-MCP is a document redaction that can be used for Sensitive Data Redaction and Sensitive Data Replacement capabilities for a wide range of documents.

It is primarily designed to be used by AI agents via MCP adapters to provide document redaction and scrubbing capabilities to intelligent document processing systems using AI agents or agentic workflows.


## Scope of the project

This project is aimed to be made into a full-fledged document "Sensitive Data Protection" MCP suite focused on inspection, de-identification and de-sensitization of documents. 
It will support various document formats such as PDF, PPT, DOCX, TXT, etc. The scope of this MCP server is to provide SDP functionalities to the AI agents and Agentic workflows.

ALso, A VERY IMPROTANT NOTE is that while this project is quite good at providing said capabilities to AI agents, but when talking about production-scenario, organisations often require much specific and customized capabilities which is out of scope of this project. 

If you are going to build a SDP tool-set or suite of enterprise level, I would suggest creating a custom capabilities pipeline, as they often see a lot more success than generic SDP suites.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) (recommended) or a Python environment with
  the dependencies installed

## How to run the server
There are two methods exposed for MCP server - STDIO and HTTP modes.

### MCP over STDIO

Use this when an MCP client launches Redaction-MCP as a local subprocess:

```bash
uv run redaction-mcp-stdio
```

For example, an MCP client command can use:

```json
{
  "command": "uv",
  "args": ["run", "--directory", "/absolute/path/to/redaction-mcp", "redaction-mcp-stdio"]
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

## Development setup
Install the application and development dependencies:

```bash
uv sync --extra dev
```

Run the test suite:

```bash
uv run python -m pytest --cov=src --cov-report=html
```

Start the Fast API server on port 8080:

```bash
uv run redaction-fastapi
```
\- or -
```bash
python -m uvicorn src.server.main:app --port 8080
```


Once running, interactive OpenAPI documentation is available at
`http://127.0.0.1:8080/docs`.

Endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check, returning `{"status":"ok"}`. |
| `POST` | `/redact` | Redact a PDF and return the result as base64. |

- Note - Also check DEVELOPMENT.md for more information on the porject development.

## Questions?
- If you have any questions or need any improvements/bug fixes, please raise a new GitHub issue stating the context.
- I'll try to follow up as soon as I can, but it may still be not very fast for some people. Please gimme time, `me slow` :)

---
### Happy Coding!!!
