"""Run the Redaction MCP server over STDIO."""

import anyio
import os

from src.cli.stdio import run_stdio_server
from src.mcp.server import redaction_mcp


def main() -> None:
    if os.name == "posix":
        anyio.run(run_stdio_server, redaction_mcp)
    else:  # pragma: no cover - exercised on Windows.
        redaction_mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
