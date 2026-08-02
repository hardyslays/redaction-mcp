"""Small, dependency-free bearer authentication for the HTTP MCP endpoint."""

from __future__ import annotations

import hmac


class BearerTokenMiddleware:
    """Require a configured bearer token for every HTTP MCP request."""

    def __init__(self, app: object, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"").decode("latin-1")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(supplied, expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"detail":"Bearer token required"}',
                    }
                )
                return
        await self.app(scope, receive, send)
