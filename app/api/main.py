"""FastAPI application: REST + WebSocket + /metrics + the built dashboard."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import live, routes
from app.api.auth import require_api_key
from app.service import Services

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    app.state.svc = await Services.create()
    app.state.arq = await create_pool(RedisSettings.from_dsn(app.state.svc.settings.redis_url))
    yield
    await app.state.arq.aclose()
    await app.state.svc.pool.close()


app = FastAPI(title="convo-intel", version="0.1.0", lifespan=lifespan,
              description="Call-center conversation intelligence: analysis, live actions, QA scoring with evidence.")
app.include_router(routes.router, dependencies=[Depends(require_api_key)])
app.include_router(live.router)

if DASHBOARD.exists():
    app.mount("/ui", StaticFiles(directory=DASHBOARD, html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/ui/" if DASHBOARD.exists() else "/docs")
