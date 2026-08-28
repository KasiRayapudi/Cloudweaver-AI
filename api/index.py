"""Vercel serverless entrypoint.

Vercel's Python runtime looks for an ASGI callable named ``app`` in files under
``api/``. This module puts ``backend/`` on the import path and re-exports the
FastAPI application, so the deployed function and the local ``uvicorn`` process
run exactly the same code.

The frontend is not served from here: ``vercel.json`` routes static assets
straight to Vercel's CDN, which is both faster and cheaper than waking a
function to hand back a stylesheet. ``app.main`` already skips mounting static
files when the directory is absent, which is the case inside the function
bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402

__all__ = ["app"]
