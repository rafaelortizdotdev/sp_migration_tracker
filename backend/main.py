"""
Migration Tracker — FastAPI backend serving API + dashboard.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import os

from backend.database import engine, Base
from backend.routes import sps, virtual_nodes


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Migration Tracker",
    description="Plan and track Snowflake object migration to Airflow/dbt",
    version="0.2.0",
    lifespan=lifespan,
)

base_dir = os.path.dirname(__file__)

static_path = os.path.join(base_dir, "frontend", "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

templates = Jinja2Templates(directory=os.path.join(base_dir, "frontend", "templates"))

app.include_router(sps.router, prefix="/api", tags=["Objects"])
app.include_router(virtual_nodes.router, prefix="/api", tags=["Virtual Nodes"])


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
