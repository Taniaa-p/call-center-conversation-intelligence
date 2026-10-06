"""Optional API-key authentication.

API_KEY empty (default) = auth off, for local demos. When set, every REST route needs the header
`X-API-Key: <key>` and the WebSocket needs `?api_key=<key>`. /health and /metrics stay open so
load balancers and Prometheus can reach them. Constant-time comparison avoids timing leaks.
"""
import secrets

from fastapi import HTTPException, Request, WebSocket

from app.settings import get_settings

OPEN_PATHS = {"/health", "/metrics"}


def key_ok(provided: str | None) -> bool:
    expected = get_settings().api_key
    return not expected or (provided is not None and secrets.compare_digest(provided, expected))


async def require_api_key(request: Request) -> None:
    if request.url.path not in OPEN_PATHS and not key_ok(request.headers.get("x-api-key")):
        raise HTTPException(401, "missing or invalid X-API-Key")


def websocket_key_ok(ws: WebSocket) -> bool:
    return key_ok(ws.query_params.get("api_key") or ws.headers.get("x-api-key"))
