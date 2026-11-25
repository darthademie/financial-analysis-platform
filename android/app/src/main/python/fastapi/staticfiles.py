"""En Android los ficheros de la interfaz los sirve el WebView desde los assets: aquí solo se guarda la configuración."""
from __future__ import annotations

from typing import Any


class StaticFiles:
    def __init__(self, *, directory: Any = None, html: bool = False, **_: Any):
        self.directory = directory
        self.html = html
