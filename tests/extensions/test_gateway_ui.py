from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saddler.extensions.gateway.server.auth import AuthMiddleware
from saddler.extensions.gateway.server.ui import mount_gateway_ui


def test_mount_gateway_ui_serves_index() -> None:
    app = FastAPI()
    mount_gateway_ui(app)
    client = TestClient(app)

    response = client.get("/ui")
    assert response.status_code == 200
    assert "Gateway 运行状态" in response.text
    assert 'fetch("/sessions/active"' in response.text


def test_ui_requires_auth() -> None:
    app = FastAPI()
    app.state.gateway_token = "secret"
    app.add_middleware(AuthMiddleware)
    mount_gateway_ui(app)
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/ui").status_code == 401

    response = client.get("/ui", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
