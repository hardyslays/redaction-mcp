"""Run the Redaction MCP server over STDIO."""

from src.mcp.server import redaction_mcp


def main() -> None:
    redaction_mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
