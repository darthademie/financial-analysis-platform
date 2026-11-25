"""Prueba el puente de Android con mensajes iguales a los que envía android-bridge.js.

Se ejecuta con el sustituto de FastAPI/Pydantic en el path (ver android/README.md), sin FastAPI instalado.
"""
import base64
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "android/app/src/main/python"), str(ROOT)]

SAMPLES = ROOT / "samples"


@pytest.fixture()
def bridge(tmp_path):
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.") or m == "finanzas_bridge"]:
        del sys.modules[name]
    module = importlib.import_module("finanzas_bridge")
    module.start(str(tmp_path))
    yield module
    module.stop()


def call(bridge, method, path, query="", **extra):
    answer = json.loads(bridge.handle(json.dumps({"id": 7, "method": method, "path": path, "query": query, "headers": {}, **extra})))
    assert answer["id"] == 7
    return answer


def upload(bridge, path, sample, **fields):
    data = base64.b64encode((SAMPLES / sample).read_bytes()).decode()
    form = {"fields": fields, "files": {"file": {"name": sample, "type": "", "data": data}}}
    return call(bridge, "POST", path, form=form)


def test_the_shim_is_used_not_real_fastapi(bridge):
    import fastapi

    assert "android" in Path(fastapi.__file__).as_posix()


def test_empty_database_starts(bridge):
    meta = json.loads(call(bridge, "GET", "/api/meta")["text"])
    assert meta["count"] == 0 and meta["accounts"] == []


def test_import_preview_commit_and_read_back(bridge):
    preview = json.loads(upload(bridge, "/api/import/preview", "bbva_ejemplo.xlsx")["text"])
    assert preview["status"] == "ok" and preview["preview"]["bank"] == "BBVA"
    done = upload(bridge, "/api/import/commit", "bbva_ejemplo.xlsx", account_name="Mi BBVA")
    assert done["status"] == 200, done
    new = json.loads(done["text"])["new"]
    assert new > 100
    meta = json.loads(call(bridge, "GET", "/api/meta")["text"])
    assert meta["count"] == new and meta["accounts"][0]["name"] == "Mi BBVA"
    dashboard = json.loads(call(bridge, "GET", "/api/dashboard", "from=2026-01-01&to=2026-10-28")["text"])
    assert dashboard["totals"]["income"] > 0
    assert json.loads(call(bridge, "GET", "/api/alerts")["text"])["items"]


def test_json_body_validation_and_errors(bridge):
    ok = call(bridge, "POST", "/api/rules", body=json.dumps({"pattern": "tienda pepe", "category_id": "ocio"}),
              headers={"content-type": "application/json"})
    assert ok["status"] == 200
    bad = call(bridge, "POST", "/api/rules", body=json.dumps({"pattern": "x", "category_id": "ocio"}))
    assert bad["status"] == 422 and isinstance(json.loads(bad["text"])["detail"], list)
    missing = call(bridge, "PATCH", "/api/alerts/999", body=json.dumps({"status": "reviewed"}))
    assert missing["status"] == 404 and json.loads(missing["text"])["detail"] == "Alerta no encontrada."
    assert call(bridge, "GET", "/api/nada")["status"] == 404


def test_csv_download_and_security_headers(bridge):
    upload(bridge, "/api/import/commit", "santander_ejemplo.xlsx")
    answer = call(bridge, "GET", "/api/transactions.csv")
    assert answer["status"] == 200
    assert answer["headers"]["content-disposition"] == 'attachment; filename="movimientos.csv"'
    assert answer["text"].startswith("﻿Fecha;Cuenta")
    assert "default-src 'self'" in answer["headers"]["content-security-policy"]


def test_origin_from_the_page_is_ignored(bridge):
    answer = call(bridge, "POST", "/api/alerts/bulk", body=json.dumps({"status": "reviewed"}),
                  headers={"origin": "https://appassets.androidplatform.net"})
    assert answer["status"] == 200
