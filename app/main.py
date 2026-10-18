"""Application factory: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .pdf.fonts import INDEX
from .pdf.text import EditError
from .store import InvalidDocument, PasswordRequired, SessionNotFound, store

log = logging.getLogger("pdf-editor")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    INDEX.start()                 # scan system fonts in the background
    removed = store.cleanup()     # drop sessions that expired while we were down
    store.start_cleanup_thread()
    log.info("PDF editor ready (data dir: %s, removed %d stale sessions)", settings.data_dir, removed)
    yield
    store.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="PDF Editor", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["*"], allow_headers=["*"],
                           expose_headers=["Content-Disposition"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    def err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": code, "message": message}, status_code=status)

    @app.exception_handler(SessionNotFound)
    async def _nf(_r, _e):
        return err(404, "not_found", "Document session not found or expired. Please open the file again.")

    @app.exception_handler(PasswordRequired)
    async def _pw(_r, _e):
        return err(401, "password_required", "This PDF is password protected.")

    @app.exception_handler(InvalidDocument)
    async def _inv(_r, e):
        return err(400, "invalid_document", str(e))

    @app.exception_handler(EditError)
    async def _edit(_r, e):
        return err(409, "edit_failed", str(e))

    @app.exception_handler(Exception)
    async def _any(_r, e):
        log.exception("Unhandled error")
        return err(500, "internal_error", f"{type(e).__name__}: {e}")

    app.include_router(router)
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
    return app


app = create_app()
