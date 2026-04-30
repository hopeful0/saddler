from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from saddler.extensions.gateway.server.auth import AuthMiddleware, build_auth_router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.gateway_token = "gateway-secret"
    app.add_middleware(AuthMiddleware)
    app.include_router(build_auth_router())

    @app.get("/protected")
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/protected/ws")
    async def protected_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"status": "ok"})
        await websocket.close()

    return app


def test_login_sets_cookie_and_redirects() -> None:
    client = TestClient(_build_app())
    response = client.get(
        "/login", params={"token": "gateway-secret"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/ui"
    set_cookie = response.headers.get("set-cookie", "")
    assert "saddler_token=gateway-secret" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie


def test_login_rejects_wrong_token() -> None:
    client = TestClient(_build_app())
    response = client.get("/login", params={"token": "wrong"})
    assert response.status_code == 401


def test_protected_allows_bearer_or_cookie() -> None:
    client = TestClient(_build_app())

    bearer_response = client.get(
        "/protected",
        headers={"Authorization": "Bearer gateway-secret"},
    )
    assert bearer_response.status_code == 200

    client.cookies.set("saddler_token", "gateway-secret")
    cookie_response = client.get("/protected")
    assert cookie_response.status_code == 200


def test_protected_unauthorized_http_returns_401() -> None:
    client = TestClient(_build_app())
    http_response = client.get("/protected")
    assert http_response.status_code == 401


def test_websocket_rejects_without_credentials_with_403() -> None:
    client = TestClient(_build_app())

    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect("/protected/ws"):
            pass

    assert exc_info.value.status_code == 403


def test_websocket_allows_bearer_or_cookie() -> None:
    client = TestClient(_build_app())

    with client.websocket_connect(
        "/protected/ws",
        headers={"Authorization": "Bearer gateway-secret"},
    ) as bearer_ws:
        assert bearer_ws.receive_json() == {"status": "ok"}

    client.cookies.set("saddler_token", "gateway-secret")
    with client.websocket_connect("/protected/ws") as cookie_ws:
        assert cookie_ws.receive_json() == {"status": "ok"}
