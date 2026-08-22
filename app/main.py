"""
FastAPI application entry point.
Initialises the app, configures CORS, registers all routers,
and provides a top-level health-check endpoint.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_TITLE, APP_VERSION
from app.routers.voice import router as voice_router

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    logger.info("🚀  %s v%s — starting up.", APP_TITLE, APP_VERSION)
    yield
    logger.info("🛑  %s — shutting down.", APP_TITLE)


# ── Application factory ───────────────────────────────────────────────────────

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=(
        "Production-grade backend for a multilingual voice-controlled shopping assistant. "
        "Powered by Groq Whisper (STT) and Llama 3.3-70B (NLP), with Gemini 1.5 Flash fallback."
    ),
    lifespan=lifespan,
)

# Allow all origins for development — restrict allowed_origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(voice_router)

# Serve the static UI — mount AFTER API routes so /api/v1/* is never shadowed
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── UI entry point ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False, tags=["UI"])
async def serve_ui() -> FileResponse:
    """Serve the single-page frontend."""
    return FileResponse("static/index.html", media_type="text/html")


# ── Health check (moved to /health so / can serve the UI) ────────────────────

@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    """Returns service health status and version."""
    return {
        "status": "healthy",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "docs": "/docs",
    }
