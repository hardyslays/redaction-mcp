"""Run the MCP server in streamable HTTP mode."""

import os

import uvicorn

from src.mcp.server import create_mcp_app

app = create_mcp_app()

def main() -> None:
    uvicorn.run(app, host=os.getenv("MCP_HOST", "127.0.0.1"), port=int(os.getenv("MCP_PORT", "8000")))


if __name__ == "__main__":
    main()
