## Tech stack information:
- Language - Python 3.12.8
- Frameworks - FastAPI (for API server), FastMCP (for MCP server), MCP[cli] (for MCP cli mode), PymuPDF (for PDF manipulation)

## Build and test commands:
- Main driver file: src/main.py
- To install required packages, use command `pip install -r requirements.txt`
- To run FastAPI server, use command `uv run redaction-fastapi`
- To run MPC server in STDIO mode, use command `uv run redaction-mcp-stdio`
- To run MPC server in HTTP mode, use command `uv run redaction-mcp-http`
- To run unit tests, use command `python -m pytest --cov=src --cov-report=html`

## Testing instructions:
- Make sure that the unit test cases are generated for all files.
- Make sure the unit tests generated are atomic, independent and mock the dependencies required.
- Ensure all test cases run successfully.

## Project structure:
src/                        # Project src folder
├── server/                    # API routes for FastAPI server
│   ├── api/
│   │   ├── endpoints/      # API endpoints exposed
│   │   └── router.py       # API router
│   ├── models/                 # FastAPI pydantic models
│   ├── services/               # Business logic
│   │   └── redaction.py
│   └── main.py                 # Application entry
├── core/                   # Core logic for redaction
│   ├── engines /           # Different redaction engines for document types
│   │   └── pdf_redaction.py    # PDF redaction engine
│   ├── models /            # Redaction pydantic models
│   ├── services/           # Service layer abstraction
│   └── utils /             # Common util methods
├── mcp /                   # MCP server implementation (SSE/HTTP mode)
└──cli /                    # MCP STDIO mode
