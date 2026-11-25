"""Piezas HTTP del sustituto de FastAPI: cabeceras, petición, respuestas y ficheros subidos."""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, MutableMapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl


class Headers(MutableMapping):
    """Cabeceras sin distinguir mayúsculas, como en Starlette."""

    def __init__(self, items: Iterable[tuple[str, str]] | dict | None = None):
        self._items: dict[str, tuple[str, str]] = {}
        if isinstance(items, dict):
            items = items.items()
        for key, value in items or ():
            self[key] = value

    def __getitem__(self, key: str) -> str:
        return self._items[key.lower()][1]

    def __setitem__(self, key: str, value: str) -> None:
        self._items[key.lower()] = (key, str(value))

    def __delitem__(self, key: str) -> None:
        del self._items[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._items

    def get(self, key: str, default: Any = None) -> Any:
        item = self._items.get(key.lower())
        return item[1] if item else default

    def raw(self) -> dict[str, str]:
        return {key: value for key, value in self._items.values()}


class QueryParams:
    def __init__(self, query: str | list[tuple[str, str]] = ""):
        self._items = parse_qsl(query, keep_blank_values=True) if isinstance(query, str) else list(query)

    def get(self, key: str, default: Any = None) -> Any:
        for name, value in self._items:
            if name == key:
                return value
        return default

    def __contains__(self, key: object) -> bool:
        return any(name == key for name, _ in self._items)

    def multi_items(self) -> list[tuple[str, str]]:
        return list(self._items)


class URL:
    def __init__(self, path: str, query: str, host: str):
        self.path = path
        self.query = query
        self.hostname = host.split(":")[0]

    def __str__(self) -> str:
        return f"http://{self.hostname}{self.path}{'?' + self.query if self.query else ''}"


class UploadFile:
    def __init__(self, filename: str | None, content: bytes, content_type: str | None = None):
        self.filename = filename
        self.content_type = content_type or "application/octet-stream"
        self._content = content
        self._position = 0
        self.size = len(content)

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._content[self._position:]
        else:
            chunk = self._content[self._position:self._position + size]
        self._position += len(chunk)
        return chunk

    async def seek(self, offset: int) -> None:
        self._position = offset

    async def close(self) -> None:
        return None


class Request:
    """Petición ya troceada. `body` es el cuerpo crudo; `form` llega ya separado en campos y ficheros."""

    def __init__(self, method: str, path: str, query: str = "", headers: dict | Headers | None = None,
                 body: bytes = b"", form: dict[str, Any] | None = None):
        self.method = method.upper()
        self.headers = headers if isinstance(headers, Headers) else Headers(headers or {})
        self.query_params = QueryParams(query)
        self.url = URL(path, query, self.headers.get("host", ""))
        self.path_params: dict[str, str] = {}
        self._body = body or b""
        self._form = form

    @property
    def path(self) -> str:
        return self.url.path

    async def body(self) -> bytes:
        return self._body

    async def json(self) -> Any:
        return json.loads(self._body)

    async def form(self) -> dict[str, Any]:
        return self._form or {}


def jsonable(value: Any) -> Any:
    """Lo mismo que `jsonable_encoder` de FastAPI para los tipos que devuelve la aplicación."""
    from pydantic import BaseModel

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(jsonable(k)): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(f"No se puede convertir a JSON: {type(value).__name__}")


class Response:
    media_type: str | None = None
    charset = "utf-8"

    def __init__(self, content: Any = None, status_code: int = 200, headers: dict | None = None, media_type: str | None = None):
        self.status_code = status_code
        if media_type is not None:
            self.media_type = media_type
        self.body = self.render(content)
        self.headers = Headers(headers or {})
        if self.media_type and "content-type" not in self.headers:
            content_type = self.media_type
            if content_type.startswith("text/") and "charset=" not in content_type:
                content_type += f"; charset={self.charset}"
            self.headers["content-type"] = content_type

    def render(self, content: Any) -> bytes:
        if content is None:
            return b""
        if isinstance(content, bytes):
            return content
        return str(content).encode(self.charset)


class JSONResponse(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(jsonable(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class PlainTextResponse(Response):
    media_type = "text/plain"


class HTMLResponse(Response):
    media_type = "text/html"


class StreamingResponse(Response):
    """Sin red de por medio no hay nada que transmitir poco a poco: se junta el contenido entero."""

    def __init__(self, content: Iterable, status_code: int = 200, headers: dict | None = None, media_type: str | None = None):
        chunks = [chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk) for chunk in content]
        super().__init__(b"".join(chunks), status_code, headers, media_type)
