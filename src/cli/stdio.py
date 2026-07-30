"""POSIX STDIO runner that remains reliable on supported Python runtimes."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

import anyio
import mcp.types as mcp_types
from mcp.server.lowlevel.server import NotificationOptions
from mcp.shared.message import SessionMessage


@asynccontextmanager
async def _descriptor_stdio_server():
    """Adapt stdin/stdout file descriptors to MCP message streams on POSIX."""
    incoming_send, incoming_receive = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    outgoing_send, outgoing_receive = anyio.create_memory_object_stream[SessionMessage](0)

    async def read_stdin() -> None:
        buffer = b""
        async with incoming_send:
            while True:
                await anyio.wait_readable(sys.stdin.fileno())
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    return
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    try:
                        message = mcp_types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:
                        await incoming_send.send(exc)
                    else:
                        await incoming_send.send(SessionMessage(message))

    async def write_stdout() -> None:
        async with outgoing_receive:
            async for session_message in outgoing_receive:
                payload = (session_message.message.model_dump_json(by_alias=True, exclude_none=True) + "\n").encode()
                # MCP responses are one JSON line.  A synchronous write avoids
                # selector registration for stdout, which is not permitted by
                # some process supervisors even though stdin is pollable.
                sys.stdout.buffer.write(payload)
                sys.stdout.buffer.flush()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(read_stdin)
        task_group.start_soon(write_stdout)
        try:
            yield incoming_receive, outgoing_send
        finally:
            task_group.cancel_scope.cancel()


async def run_stdio_server(server: object) -> None:
    """Run FastMCP over descriptor-based STDIO on POSIX systems."""
    from fastmcp.server.context import reset_transport, set_transport

    token = set_transport("stdio")
    try:
        async with server._lifespan_manager():  # type: ignore[attr-defined]
            async with _descriptor_stdio_server() as (read_stream, write_stream):
                mcp_server = server._mcp_server  # type: ignore[attr-defined]
                await mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp_server.create_initialization_options(
                        notification_options=NotificationOptions(tools_changed=True)
                    ),
                )
    finally:
        reset_transport(token)
