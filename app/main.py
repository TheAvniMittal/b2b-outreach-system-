"""
FastAPI Application Entrypoint (VISION.md §18.3).
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from app.api.routers.dashboard import router as dashboard_router
from app.api.routers.management import router as management_router
from app.api.routers.tone_studio import router as tone_studio_router
from app.pipeline.sweeper import sweeper_daemon
from app.pipeline.dispatch_worker import dispatch_worker
from app.pipeline.imap_listener import imap_poller_daemon

app = FastAPI(
    title="Narayani Outreach System API",
    description="Operational Dashboard and Control API for Narayani B2B Cold Outreach Pipeline",
    version="1.0.0",
)


@app.on_event("startup")
def startup_event():
    # Launches recovery sweeper daemon (runs startup sweep once, then periodic loop)
    sweeper_daemon.start()
    # Launches outbound dispatch worker (processes queue items when sending is active)
    dispatch_worker.start()
    # Launches background IMAP poller for inbound prospect replies
    imap_poller_daemon.start()


@app.on_event("shutdown")
def shutdown_event():
    imap_poller_daemon.stop()
    dispatch_worker.stop()
    sweeper_daemon.stop()


# Static directory setup
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount Routers
app.include_router(dashboard_router)
app.include_router(management_router)
app.include_router(tone_studio_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", response_class=FileResponse)
@app.get("/app", response_class=FileResponse)
@app.get("/app/", response_class=FileResponse)
@app.get("/ui", response_class=FileResponse)
@app.get("/ui/", response_class=FileResponse)
def app_ui():
    """Serves the full multi-screen operational SPA UI."""
    html_path = STATIC_DIR / "index.html"
    return FileResponse(str(html_path))


@app.get("/dashboard-ui", response_class=FileResponse)
def dashboard_ui():
    """Serves the minimal visual operational dashboard page."""
    html_path = STATIC_DIR / "dashboard.html"
    return FileResponse(str(html_path))


@app.get("/api/info")
def root_info():
    return {
        "system": "Narayani Distributors B2B Outreach System",
        "status": "operational",
        "docs_url": "/docs",
        "ui_app": "/app",
        "dashboard_api": "/api/dashboard",
        "dashboard_ui": "/dashboard-ui",
    }

