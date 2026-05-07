from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_TUI_DIR = _STATIC_DIR / "tui"


def mount_gateway_ui(app: FastAPI) -> None:
    app.mount(
        "/ui",
        StaticFiles(directory=str(_STATIC_DIR), html=True),
        name="gateway-ui",
    )
    app.mount(
        "/tui",
        StaticFiles(directory=str(_TUI_DIR), html=True),
        name="gateway-tui",
    )
