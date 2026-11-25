"""Mismo control de cabecera Host que `starlette.middleware.trustedhost`, como middleware del sustituto de FastAPI."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from fastapi._http import PlainTextResponse, Request, Response


class TrustedHostMiddleware:
    def __init__(self, app: object = None, allowed_hosts: Sequence[str] | None = None, www_redirect: bool = True):
        self.allowed_hosts = list(allowed_hosts or ["*"])
        self.allow_any = "*" in self.allowed_hosts

    async def __call__(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if self.allow_any:
            return await call_next(request)
        host = request.headers.get("host", "").split(":")[0]
        for pattern in self.allowed_hosts:
            if host == pattern or (pattern.startswith("*") and host.endswith(pattern[1:])):
                return await call_next(request)
        return PlainTextResponse("Invalid host header", status_code=400)
