from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.requests import HTTPConnection


def _extract_bearer_token(conn: HTTPConnection) -> str | None:
    authorization = conn.headers.get("authorization")
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class AuthMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        conn = HTTPConnection(scope)
        if (
            scope_type == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/login"
        ):
            await self.app(scope, receive, send)
            return

        gateway_token = conn.app.state.gateway_token
        bearer_token = _extract_bearer_token(conn)
        cookie_token = conn.cookies.get("saddler_token")
        if secrets.compare_digest(
            bearer_token or "", gateway_token
        ) or secrets.compare_digest(cookie_token or "", gateway_token):
            await self.app(scope, receive, send)
            return

        if scope_type == "http":
            response = Response(status_code=401)
            await response(scope, receive, send)
            return

        await self._reject_websocket(scope, send)

    async def _reject_websocket(self, scope, send) -> None:
        try:
            await send(
                {
                    "type": "websocket.http.response.start",
                    "status": 403,
                    "headers": [],
                }
            )
            await send(
                {
                    "type": "websocket.http.response.body",
                    "body": b"",
                }
            )
            return
        except RuntimeError:
            # Some ASGI servers don't support websocket.http.response.* events;
            # fall back to a policy-violation close code.
            await send({"type": "websocket.close", "code": 1008})


def build_auth_router() -> APIRouter:
    router = APIRouter()

    @router.get("/login")
    async def login(request: Request, token: str) -> RedirectResponse:
        gateway_token = request.app.state.gateway_token
        if not secrets.compare_digest(token, gateway_token):
            raise HTTPException(status_code=401, detail="unauthorized")

        response = RedirectResponse(url="/ui", status_code=302)
        response.set_cookie(
            key="saddler_token",
            value=token,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    return router
