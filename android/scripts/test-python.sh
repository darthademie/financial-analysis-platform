#!/bin/sh
# Pasa la suite de tests/ y la del puente contra el sustituto de FastAPI/Pydantic de Android, sin FastAPI instalado.
# Uso (desde la raíz del repositorio): sh android/scripts/test-python.sh   — necesita podman o docker.
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ENGINE=$(command -v podman || command -v docker)
MSYS_NO_PATHCONV=1 "$ENGINE" run --rm -v "$ROOT:/src:ro" -w /src \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/src/android/app/src/main/python \
  docker.io/library/python:3.12-slim sh -c '
    pip install -q --disable-pip-version-check --root-user-action=ignore \
      openpyxl==3.1.5 xlrd==2.0.2 defusedxml==0.7.1 xlwt pytest &&
    python -m pytest -q -p no:cacheprovider tests &&
    python -m pytest -q -p no:cacheprovider android/tests'
