"""Run the MCP server in streamable HTTP mode."""

import argparse
import os
import uvicorn
from redaction_mcp.mcp.server import create_mcp_app
from dotenv import load_dotenv


load_dotenv()

app = create_mcp_app(os.getenv("MCP_AUTH_TOKEN"))


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MCP HTTP server")
    parser.add_argument(
        "--port", type=int, default=os.getenv("MCP_PORT", "8000"), help="Listening port (overrides MCP_PORT)"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    auth_token = os.getenv("MCP_AUTH_TOKEN")
    if not _is_loopback(host) and not auth_token:
        raise RuntimeError("MCP_AUTH_TOKEN is required when MCP_HOST is not a loopback address")
    uvicorn.run(
        create_mcp_app(auth_token),
        host=host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
