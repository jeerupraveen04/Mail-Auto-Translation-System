"""FastAPI application factory wiring cache, worker pool, store, pipeline."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cache import TranslationCache
from .db import TemplateStore
from .pipeline import TranslationPipeline
from .routes import router
from .worker import TranslationWorkerPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "frontend")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Multilingual Course Mail Auto-Translation System",
        version="1.0.0",
        description=(
            "3-tier waterfall cascade (DeepL > AWS Translate > Google Gemini) "
            "for 38-locale course mail auto-translation."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.cache = TranslationCache()
    app.state.pool = TranslationWorkerPool()
    app.state.store = TemplateStore()
    app.state.pipeline = TranslationPipeline()
    app.include_router(router)

    @app.on_event("startup")
    def _startup() -> None:
        try:
            app.state.store.ensure_schema()
            _logger.info("PostgreSQL schema ready: %s", app.state.store.database_url_hostport())
        except Exception as exc:  # noqa: BLE001 - don't crash without DB
            _logger.error("Database unavailable (%s); persistence disabled until connection returns", exc)

    @app.get("/", include_in_schema=False)
    def studio() -> FileResponse:
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="frontend")
    return app


app = create_app()
