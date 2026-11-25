"""`TestClient` compatible con el de FastAPI, para pasar la suite de `tests/` contra este sustituto.

No se usa en la app: existe para comprobar que el sustituto se comporta igual que FastAPI (ver `android/README.md`).
"""
from __future__ import annotations

import asyncio
import json as jsonlib
from typing import Any
from urllib.parse import urlencode, urlsplit

from ._http import Headers, Request, UploadFile


class TestResponse:
    def __init__(self, status_code: int, headers: Headers, content: bytes):
        self.status_code = status_code
        self.headers = headers
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> Any:
        return jsonlib.loads(self.content)


class TestClient:
    __test__ = False  # que pytest no la confunda con una clase de pruebas

    def __init__(self, app: Any, base_url: str = "http://testserver"):
        self.app = app
        self.host = urlsplit(base_url).netloc
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lifespan = None

    def __enter__(self) -> "TestClient":
        self._loop = asyncio.new_event_loop()
        if self.app.lifespan:
            self._lifespan = self.app.lifespan(self.app)
            self._loop.run_until_complete(self._lifespan.__aenter__())
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._lifespan is not None:
            self._loop.run_until_complete(self._lifespan.__aexit__(None, None, None))
        self._loop.close()
        self._loop = None

    def request(self, method: str, url: str, *, params: dict | None = None, json: Any = None, data: dict | None = None,
                files: dict | None = None, headers: dict | None = None) -> TestResponse:
        parts = urlsplit(url)
        query = parts.query
        if params:
            extra = urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
            query = f"{query}&{extra}" if query else extra
        all_headers = Headers({"host": self.host, "user-agent": "testclient"})
        for key, value in (headers or {}).items():
            all_headers[key] = value
        body, form = b"", None
        if json is not None:
            body = jsonlib.dumps(json).encode()
            all_headers["content-type"] = "application/json"
        if data is not None or files is not None:
            form = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in (data or {}).items()}
            for key, spec in (files or {}).items():
                name, content, *rest = spec
                if hasattr(content, "read"):
                    content = content.read()
                form[key] = UploadFile(name, content, rest[0] if rest else None)
            all_headers["content-type"] = "multipart/form-data"
        request = Request(method, parts.path, query, all_headers, body, form)
        loop = self._loop or asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(self.app.handle(request))
        finally:
            if self._loop is None:
                loop.close()
        return TestResponse(response.status_code, response.headers, response.body)

    def get(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("DELETE", url, **kwargs)
