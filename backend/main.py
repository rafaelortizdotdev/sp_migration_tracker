"""
SP Migration Tracker - FastAPI Backend
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Request
from contextlib import asynccontextmanager
import os

from backend.database import engine, Base
from backend.routes import sps, virtual_nodes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup"""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="SP Migration Tracker",
    description="Track and plan Snowflake SP migration to Airflow/dbt",
    version="0.1.0",
    lifespan=lifespan
)

# Mount static files
static_path = os.path.join(os.path.dirname(__file__), "frontend", "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Templates
templates_path = os.path.join(os.path.dirname(__file__), "frontend", "templates")
templates = Jinja2Templates(directory=templates_path)

# Include API routes
app.include_router(sps.router, prefix="/api", tags=["Stored Procedures"])
app.include_router(virtual_nodes.router, prefix="/api", tags=["Virtual Nodes"])


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard"""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}
