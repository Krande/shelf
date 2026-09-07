from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .api import (
    annotations,
    attachments,
    auth,
    collections,
    export,
    extraction,
    items,
    me,
    notes,
    processing,
    tags,
    tokens,
    v1,
    worker_api,
)
from .config import settings

app = FastAPI(title="Shelf", version=__version__)

# Required by authlib's OIDC code-flow client to stash PKCE/state across
# the redirect. Separate from the application's own session JWT cookie —
# this one is short-lived and only active during the OAuth handshake.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    https_only=settings.session_cookie_secure,
    same_site="lax",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(items.router)
app.include_router(collections.router)
app.include_router(attachments.router)
app.include_router(annotations.router)
app.include_router(export.router)
app.include_router(extraction.router)
app.include_router(notes.router)
app.include_router(processing.router)
app.include_router(tags.router)
app.include_router(tokens.router)
app.include_router(v1.router)
app.include_router(worker_api.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api")
async def api_root() -> dict[str, str]:
    return {
        "name": "shelf",
        "version": __version__,
        "image_tag": settings.image_tag,
    }


# ── SPA hosting ──────────────────────────────────────────────────────────────
# In production the built frontend lives at /app/frontend (configured via
# SHELF_FRONTEND_DIR in the container). In dev the SPA runs separately on
# vite's :5173 and proxies /api + /auth back here, so we just skip the
# mount when the dist directory is absent.

_frontend_dir = Path(settings.frontend_dir)
if _frontend_dir.is_dir():
    _assets_dir = _frontend_dir / "assets"
    if _assets_dir.is_dir():
        app.mount(
            "/assets", StaticFiles(directory=_assets_dir), name="frontend-assets"
        )

    _index = _frontend_dir / "index.html"

    # response_model=None — FastAPI otherwise tries to build a Pydantic
    # field from the union return type and chokes on FileResponse, which
    # isn't a Pydantic model.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def spa_fallback(
        full_path: str, request: Request
    ) -> FileResponse | JSONResponse:
        # Anything that looks like an API/auth route should 404 cleanly
        # rather than silently fall through to index.html.
        if full_path.startswith(("api/", "auth/", "health")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Pass static files (favicon, robots.txt, etc.) through if present.
        candidate = _frontend_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_index)
