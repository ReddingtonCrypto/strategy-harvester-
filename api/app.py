"""
FastAPI application setup for StrategyHarvester.

Exposes Strategy Card CRUD endpoints for the future React frontend. The
database schema is created on startup, and routers are mounted from the
`routes/` package.

Run with:
    uvicorn api.app:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import backtesting, learning, signals, strategies
from storage import strategy_store

app = FastAPI(
    title="StrategyHarvester API",
    version="1.0.0",
    description="Backend engine for the AI-powered Strategy Intelligence System.",
)

# Allow the local frontend (and any origin for now) to call the API.
# Tighten this in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup() -> None:
    """Ensure the database is ready before serving requests."""
    strategy_store.init()


@app.get("/", tags=["health"])
def root() -> dict:
    """Simple health-check / welcome endpoint."""
    return {"app": "StrategyHarvester", "version": "1.0.0", "status": "ok"}


# Mount routers.
app.include_router(strategies.router)
app.include_router(backtesting.router)
app.include_router(signals.router)
app.include_router(learning.router)
app.include_router(learning.performance_router)
app.include_router(learning.regime_router)
app.include_router(learning.optimization_router)
