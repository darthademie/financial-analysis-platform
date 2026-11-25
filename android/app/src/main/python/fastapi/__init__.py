"""Sustituto mínimo de FastAPI para ejecutar `app/main.py` dentro de la app Android, sin servidor ni puerto.

FastAPI depende de pydantic-core, que no existe para Android en Chaquopy. Este paquete implementa el subconjunto que
usa la aplicación (rutas, parámetros de consulta/formulario/fichero/cuerpo, dependencias con `yield`, middleware,
`lifespan`, `HTTPException`) con el mismo comportamiento observable: mismos códigos de estado, mismo formato de error
`{"detail": ...}` y mismo orden de middleware. El WebView le pasa cada petición con `FastAPI.handle()`.

La suite de `tests/` se ejecuta también contra este sustituto (ver `android/README.md`) para detectar diferencias.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import traceback
import types
import typing
from collections.abc import Callable
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ValidationError, validate_value

from ._http import (
    Headers,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Request,
    Response,
    StreamingResponse,
    UploadFile,
)

__all__ = [
    "Depends", "FastAPI", "File", "Form", "HTTPException", "Path", "Query", "Request", "Response", "UploadFile",
    "Headers", "HTMLResponse", "JSONResponse", "PlainTextResponse", "StreamingResponse",
]

_MISSING = object()


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any = None, headers: dict | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail if detail is not None else _REASONS.get(status_code, "")
        self.headers = headers


_REASONS = {400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
            422: "Unprocessable Entity", 500: "Internal Server Error"}


# ---------------------------------------------------------------- declaración de parámetros


class _Param:
    source = ""

    def __init__(self, default: Any = _MISSING, *, alias: str | None = None, **constraints: Any):
        self.default = _MISSING if default is Ellipsis else default
        self.alias = alias
        self.constraints = {k: v for k, v in constraints.items()
                            if k in ("gt", "ge", "lt", "le", "min_length", "max_length", "pattern") and v is not None}


class _Query(_Param):
    source = "query"


class _Path(_Param):
    source = "path"


class _Form(_Param):
    source = "form"


class _File(_Param):
    source = "file"


class _Depends:
    def __init__(self, dependency: Callable):
        self.dependency = dependency


def Query(default: Any = _MISSING, **kwargs: Any) -> Any:  # noqa: N802 - mismos nombres que FastAPI
    return _Query(default, **kwargs)


def Path(default: Any = _MISSING, **kwargs: Any) -> Any:  # noqa: N802
    return _Path(default, **kwargs)


def Form(default: Any = _MISSING, **kwargs: Any) -> Any:  # noqa: N802
    return _Form(default, **kwargs)


def File(default: Any = _MISSING, **kwargs: Any) -> Any:  # noqa: N802
    return _File(default, **kwargs)


def Depends(dependency: Callable) -> Any:  # noqa: N802
    return _Depends(dependency)


# ---------------------------------------------------------------- rutas


_PARAM_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)(?::(path))?}")


def _is_body_type(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return any(_is_body_type(a) for a in get_args(annotation) if a is not type(None))
    if origin in (list, dict, typing.List, typing.Dict):
        return True
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


class _Route:
    def __init__(self, method: str, path: str, endpoint: Callable):
        self.method = method
        self.path = path
        self.endpoint = endpoint
        pattern = _PARAM_RE.sub(lambda m: f"(?P<{m[1]}>.*)" if m[2] else f"(?P<{m[1]}>[^/]+)", re.escape(path).replace(r"\{", "{").replace(r"\}", "}"))
        self.regex = re.compile(f"^{pattern}$")
        self.signature = inspect.signature(endpoint)
        self.hints = get_type_hints(endpoint)
        self.hints.pop("return", None)

    def match(self, path: str) -> dict[str, str] | None:
        found = self.regex.match(path)
        return found.groupdict() if found else None


class FastAPI:
    def __init__(self, *, title: str = "", lifespan: Callable | None = None, **_: Any):
        self.title = title
        self.lifespan = lifespan
        self.routes: list[_Route] = []
        self.mounts: dict[str, Any] = {}
        # Como en Starlette: el último middleware añadido es el más externo.
        self.middleware_stack: list[Callable] = []

    # ---- registro

    def add_api_route(self, path: str, endpoint: Callable, methods: list[str]) -> None:
        for method in methods:
            self.routes.append(_Route(method.upper(), path, endpoint))

    def _route(self, method: str, path: str, **_: Any) -> Callable:
        def decorator(endpoint: Callable) -> Callable:
            self.add_api_route(path, endpoint, [method])
            return endpoint
        return decorator

    def get(self, path: str, **kwargs: Any) -> Callable:
        return self._route("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Callable:
        return self._route("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Callable:
        return self._route("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Callable:
        return self._route("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Callable:
        return self._route("DELETE", path, **kwargs)

    def add_middleware(self, middleware_class: type, **options: Any) -> None:
        self.middleware_stack.insert(0, middleware_class(**options))

    def middleware(self, kind: str) -> Callable:
        if kind != "http":
            raise ValueError("Solo se admite middleware 'http'.")

        def decorator(function: Callable) -> Callable:
            self.middleware_stack.insert(0, function)
            return function
        return decorator

    def mount(self, path: str, app: Any, name: str | None = None) -> None:
        # Los ficheros estáticos los sirve el WebView desde los assets de la app.
        self.mounts[path] = app

    # ---- ejecución

    async def handle(self, request: Request) -> Response:
        """Punto de entrada: la petición atraviesa el middleware y llega a su ruta."""
        async def call(index: int, req: Request) -> Response:
            if index == len(self.middleware_stack):
                return await self._dispatch(req)
            return await self.middleware_stack[index](req, lambda r: call(index + 1, r))

        try:
            return await call(0, request)
        except Exception:  # lo que en Starlette atrapa ServerErrorMiddleware, por fuera de todo
            traceback.print_exc(file=sys.stderr)
            return PlainTextResponse("Internal Server Error", status_code=500)

    async def _dispatch(self, request: Request) -> Response:
        partial: _Route | None = None
        for route in self.routes:
            params = route.match(request.path)
            if params is None:
                continue
            if route.method != request.method and not (request.method == "HEAD" and route.method == "GET"):
                partial = partial or route
                continue
            request.path_params = params
            return await self._run(route, request)
        if partial is not None:
            return JSONResponse({"detail": "Method Not Allowed"}, status_code=405)
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    async def _run(self, route: _Route, request: Request) -> Response:
        cleanups: list[Callable] = []
        try:
            try:
                kwargs = await self._arguments(route, request, cleanups)
                result = route.endpoint(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except _RequestValidation as exc:
                return JSONResponse({"detail": exc.errors}, status_code=422)
            if isinstance(result, Response):
                return result
            return JSONResponse(result)
        finally:
            for cleanup in reversed(cleanups):
                cleanup()

    async def _arguments(self, route: _Route, request: Request, cleanups: list[Callable]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        errors: list[dict] = []
        body_cache: dict[str, Any] = {}

        async def body_json() -> Any:
            if "value" not in body_cache:
                raw = await request.body()
                if not raw:
                    body_cache["value"] = _MISSING
                else:
                    try:
                        body_cache["value"] = json.loads(raw)
                    except ValueError as exc:
                        raise _RequestValidation([{"type": "json_invalid", "loc": ["body", 0], "msg": "JSON decode error",
                                                   "input": {}, "ctx": {"error": str(exc)}}]) from None
            return body_cache["value"]

        for name, parameter in route.signature.parameters.items():
            annotation = route.hints.get(name, Any)
            default = parameter.default
            if isinstance(default, _Depends):
                kwargs[name] = self._resolve_dependency(default.dependency, cleanups)
                continue
            if annotation is Request:
                kwargs[name] = request
                continue
            if isinstance(default, _Param):
                spec = default
            elif name in request.path_params:
                spec = _Path()
            elif _is_body_type(annotation):
                spec = None
            else:
                spec = _Query(_MISSING if default is inspect.Parameter.empty else default)

            if spec is None:  # cuerpo JSON completo
                value = await body_json()
                if value is _MISSING:
                    if default is inspect.Parameter.empty:
                        errors.append({"type": "missing", "loc": ["body"], "msg": "Field required", "input": None})
                    else:
                        kwargs[name] = default
                    continue
                try:
                    kwargs[name] = validate_value(value, annotation, None, ("body",))
                except ValidationError as exc:
                    errors.extend(exc.errors())
                continue

            key = spec.alias or name
            if spec.source == "path":
                raw, present = request.path_params.get(key), key in request.path_params
            elif spec.source == "query":
                raw, present = request.query_params.get(key), key in request.query_params
            else:
                form = await request.form()
                raw, present = form.get(key), key in form
                if spec.source == "form" and (isinstance(raw, UploadFile) or raw == ""):
                    present = False  # FastAPI trata un campo de formulario vacío como ausente
                if spec.source == "file" and not isinstance(raw, UploadFile):
                    present = False
            if not present:
                if spec.default is _MISSING:
                    errors.append({"type": "missing", "loc": [spec.source if spec.source != "file" else "body", key],
                                   "msg": "Field required", "input": None})
                else:
                    kwargs[name] = spec.default
                continue
            if spec.source == "file":
                kwargs[name] = raw
                continue
            try:
                kwargs[name] = validate_value(raw, annotation, spec.constraints,
                                              (spec.source if spec.source != "form" else "body", key))
            except ValidationError as exc:
                errors.extend(exc.errors())
        if errors:
            raise _RequestValidation(errors)
        return kwargs

    @staticmethod
    def _resolve_dependency(dependency: Callable, cleanups: list[Callable]) -> Any:
        if inspect.isgeneratorfunction(dependency):
            generator = dependency()
            value = next(generator)

            def close() -> None:
                try:
                    next(generator)
                except StopIteration:
                    pass
            cleanups.append(close)
            return value
        return dependency()


class _RequestValidation(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("validación")
        self.errors = errors
