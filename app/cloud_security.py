"""Cloud entrypoint: password-protected same-origin React + Fantasy Coach API.

Runs only when called as ``uvicorn deploy_api:app --app-dir app``; the local
``api:app`` development entrypoint is unchanged.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

PUBLIC_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/subscriptions/confirm"),
    ("GET", "/api/subscriptions/unsubscribe"),
    ("POST", "/api/subscriptions/unsubscribe"),
}


def _authorized(header: str | None, username: str, password: str) -> bool:
    """Compare both username and password in constant time."""
    if not header or len(header) > 4096 or not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        given_user, given_password = raw.split(":", 1)
    except (binascii.Error, UnicodeError, ValueError):
        return False
    return (hmac.compare_digest(given_user.encode(), username.encode())
            & hmac.compare_digest(given_password.encode(), password.encode())) == 1


def configure_cloud_app(app: FastAPI, *, static_dir: Path | None = None) -> FastAPI:
    user = os.environ.get("FANTASY_WEB_USERNAME", "")
    password = os.environ.get("FANTASY_WEB_PASSWORD", "")
    if not user or ":" in user or len(password) < 18:
        raise RuntimeError("Set FANTASY_WEB_USERNAME and a strong FANTASY_WEB_PASSWORD (18+ characters) before cloud deployment.")

    @app.middleware("http")
    async def cloud_password(request: Request, call_next):
        path = request.url.path
        if (request.method, path) not in PUBLIC_ROUTES and not _authorized(
            request.headers.get("authorization"), user, password
        ):
            return PlainTextResponse(
                "Authentication required", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Fantasy Coach"', "Cache-Control": "no-store"},
            )
        # The Capacitor-v8 patch protects remote /api calls with a mobile key.
        # An authenticated same-origin browser cannot supply that key, so this
        # outer Basic-auth middleware attaches it for the inner API guard only.
        # A client-supplied mobile key is discarded to prevent spoofed values.
        if path.startswith('/api/') and (mobile_key := os.getenv('FANTASY_MOBILE_ACCESS_KEY', '')):
            if len(mobile_key) < 32:
                return PlainTextResponse('Invalid server mobile-access configuration', status_code=503)
            headers = [(k, v) for k, v in request.scope['headers'] if k.lower() != b'x-fantasy-mobile-key']
            headers.append((b'x-fantasy-mobile-key', mobile_key.encode('ascii')))
            request.scope['headers'] = headers
        response: Response = await call_next(request)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    directory = Path(static_dir) if static_dir else Path(__file__).resolve().parents[1] / "web" / "dist"
    directory = directory.resolve()
    if not (directory / "index.html").is_file():
        raise RuntimeError(f"Production React build missing: expected {directory / 'index.html'}")
    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="cloud-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def react_spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, detail="API endpoint not found")
        candidate = (directory / path).resolve()
        if candidate.is_relative_to(directory) and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(directory / "index.html"))

    return app


