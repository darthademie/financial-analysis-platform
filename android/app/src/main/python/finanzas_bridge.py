"""Puente entre el WebView de Android y `app.main`, sin servidor HTTP ni puerto abierto.

`MainActivity` llama a `start()` una vez y a `handle()` por cada petición `/api/...` que intercepta
`android-bridge.js`. Todo se ejecuta en un único hilo de Java, así que aquí no hay concurrencia.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import traceback

# Las peticiones llegan del propio WebView: el Host es siempre este y no se envía Origin.
HOST = "localhost"

_loop: asyncio.AbstractEventLoop | None = None
_app = None
_lifespan = None


def start(data_dir: str) -> None:
    """Abre (o crea y actualiza) la base de datos en el almacenamiento privado de la app."""
    global _loop, _app, _lifespan
    if _app is not None:
        return
    os.environ["DATA_DIR"] = data_dir
    os.environ["ALLOWED_HOSTS"] = HOST
    from app import main

    _loop = asyncio.new_event_loop()
    _app = main.app
    _lifespan = _app.lifespan(_app) if _app.lifespan else None
    if _lifespan is not None:
        _loop.run_until_complete(_lifespan.__aenter__())


def stop() -> None:
    global _app, _lifespan
    if _lifespan is not None and _loop is not None:
        _loop.run_until_complete(_lifespan.__aexit__(None, None, None))
    _app = _lifespan = None


def handle(message: str) -> str:
    """Recibe la petición en JSON y devuelve la respuesta en JSON (ver android-bridge.js)."""
    from fastapi import Request, UploadFile

    envelope = json.loads(message)
    request_id = envelope.get("id")
    try:
        headers = {k.lower(): str(v) for k, v in (envelope.get("headers") or {}).items()}
        headers["host"] = HOST
        headers.pop("origin", None)
        body = (envelope.get("body") or "").encode("utf-8")
        form = None
        if envelope.get("form") is not None:
            form = dict(envelope["form"].get("fields") or {})
            for key, spec in (envelope["form"].get("files") or {}).items():
                form[key] = UploadFile(spec.get("name"), base64.b64decode(spec.get("data") or ""), spec.get("type"))
        request = Request(envelope.get("method", "GET"), envelope.get("path", "/"), envelope.get("query") or "",
                          headers, body, form)
        response = _loop.run_until_complete(_app.handle(request))
        content_type = response.headers.get("content-type", "")
        textual = content_type.startswith(("application/json", "text/"))
        return json.dumps({
            "id": request_id,
            "status": response.status_code,
            "headers": {key.lower(): value for key, value in response.headers.raw().items()},
            "text" if textual else "base64":
                response.body.decode("utf-8") if textual else base64.b64encode(response.body).decode("ascii"),
        }, ensure_ascii=False)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return json.dumps({"id": request_id, "status": 500, "headers": {"content-type": "text/plain"},
                           "text": "Internal Server Error"})
