"""Command-line port selection for the HTTP server entry points."""

import importlib
import sys
from unittest.mock import Mock

import pytest


ENTRYPOINTS = [
    ("redaction_mcp.mcp.main", "redaction-mcp-http", "MCP_PORT", 8000),
    ("redaction_mcp.server.main", "redaction-fastapi", "FASTAPI_PORT", 8080),
]


@pytest.mark.parametrize("module_name,command,port_env,default_port", ENTRYPOINTS)
@pytest.mark.parametrize(
    "env_port,arguments,expected_port",
    [
        (None, [], None),
        ("9100", [], 9100),
        ("9100", ["--port", "9200"], 9200),
    ],
)
def test_http_entrypoint_uses_selected_port(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    command: str,
    port_env: str,
    default_port: int,
    env_port: str | None,
    arguments: list[str],
    expected_port: int | None,
) -> None:
    launcher = importlib.import_module(module_name)
    run = Mock()
    monkeypatch.setattr(launcher.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", [command, *arguments])
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("FASTAPI_HOST", "127.0.0.1")
    if env_port is None:
        monkeypatch.delenv(port_env, raising=False)
    else:
        monkeypatch.setenv(port_env, env_port)

    launcher.main()

    run.assert_called_once()
    assert run.call_args.kwargs["port"] == (default_port if expected_port is None else expected_port)


@pytest.mark.parametrize(
    "module_name,command",
    [
        ("redaction_mcp.mcp.main", "redaction-mcp-http"),
        ("redaction_mcp.server.main", "redaction-fastapi"),
    ],
)
@pytest.mark.parametrize("invalid_port", ["0", "65536", "not-a-port"])
def test_http_entrypoint_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    command: str,
    invalid_port: str,
) -> None:
    launcher = importlib.import_module(module_name)
    run = Mock()
    monkeypatch.setattr(launcher.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", [command, "--port", invalid_port])

    with pytest.raises(SystemExit) as exc:
        launcher.main()

    assert exc.value.code == 2
    run.assert_not_called()
