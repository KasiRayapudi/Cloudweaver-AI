"""FastAPI application entry point.

Serves the JSON API under ``/api`` and the single-page frontend at ``/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import VERSION, router
from app.config import get_settings

logging.basicConfig(
    level=logging.DEBUG if get_settings().debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class RevalidatingStatic(StaticFiles):
    """Serve frontend assets with a must-revalidate policy.

    The default StaticFiles sends no cache directives, so a browser is free to
    reuse a module indefinitely. That produced a genuinely confusing failure
    during development: an edited module kept running its previous version,
    and the symptom -- a control that does nothing -- looks exactly like a
    logic bug rather than a stale asset.

    "no-cache" still permits caching; it requires the browser to revalidate
    first, so unchanged files come back as a 304 and cost nothing.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

app = FastAPI(
    title="AI-Driven Infrastructure Diagram and IaC Generator",
    description=(
        "Turns a plain-language infrastructure requirement into an architecture "
        "diagram and matching Terraform, both generated from one shared "
        "representation so they cannot drift apart."
    ),
    version=VERSION,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

if FRONTEND_DIR.is_dir():
    app.mount("/static", RevalidatingStatic(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            FRONTEND_DIR / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
