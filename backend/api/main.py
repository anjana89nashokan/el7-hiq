from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import (
    dart_suggestion,
    data,
    doc_extraction,
    evidencehub,
    extracts,
    extract_driver,
    files,
    graphs,
    indemap,
    logs,
    mapping,
    messages,
    messages_stream,
    messages_stream_new,
    quality,
    sessions,
)
from api.routers import (
    sessions,
    files,
    messages,
    logs,
    messages_stream,
    messages_stream_new,
    data,
)
from api.routers import mapping, indemap
from api.routers import mapping
from api.routers import evidencehub
from api.routers import graphs
from api.routers import mapping, indemap, dart_suggestion
from api.routers import settings as settings_router
from api.routers import hl7
from datetime import datetime
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging


app = FastAPI()
logger = logging.getLogger(__name__)

# Configure CORS. Default to the local Launchpad + DataMap dev origins; override
# in deployment via DATAMAP_CORS_ORIGINS (comma-separated). The "*" wildcard was
# removed so the SSO Authorization header is only honored for known origins.
import os as _os

_default_origins = [
    "http://localhost:3000",   # Launchpad dev
    "http://localhost:5173",   # DataMap (Vite) dev
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]
_configured = (_os.getenv("DATAMAP_CORS_ORIGINS") or "").strip()
origins = (
    [o.strip() for o in _configured.split(",") if o.strip()]
    if _configured
    else _default_origins
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # allow all HTTP methods
    allow_headers=["*"],  # allow all headers
)

# app.mount("/reports", StaticFiles(directory="reports"), name="reports")
# =====================================================================

# 1. Get the path to this main.py file's directory (server/api)
current_file_dir = Path(__file__).parent

# 2. Get the path to the 'server' root by going up one level
server_root = current_file_dir.parent

# 3. Construct the absolute path to the 'reports' directory
reports_dir = server_root / "reports"
print(
    f"\n--- [SERVER STARTUP] Serving static files from directory: {reports_dir} ---\n"
)

reports_dir.mkdir(exist_ok=True)  # Create dir if it doesn't exist

# 4. Mount the '/reports' URL path to the absolute 'reports_dir' on disk
#    This tells FastAPI how to serve the HTML files.
app.mount("/reports", StaticFiles(directory=reports_dir), name="reports")

# =====================================================================


@app.on_event("startup")
def _init_local_db() -> None:
    """Create local SQLite app tables on startup (standalone mode)."""
    try:
        from db.engine import init_db

        init_db()
        print("--- [SERVER STARTUP] Local app DB initialized ---")
    except Exception as exc:  # noqa: BLE001
        print(f"--- [SERVER STARTUP] App DB init failed: {exc} ---")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# Include routers
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
app.include_router(files.router, prefix="/files", tags=["files"])
app.include_router(messages.router, prefix="/messages", tags=["messages"])
app.include_router(
    messages_stream.router, prefix="/messages-strm", tags=["messages_stream"]
)
app.include_router(
    messages_stream_new.router, prefix="/messages-strm", tags=["messages_stream_new"]
)  # NEW: Enhanced streaming endpoint
app.include_router(logs.router, prefix="/logs", tags=["logs"])
app.include_router(mapping.router, prefix="/mapping", tags=["mapping"])
app.include_router(evidencehub.router, prefix="/evidencehub", tags=["evidencehub"])
app.include_router(graphs.router, prefix="/graphs", tags=["graphs"])
app.include_router(data.router, prefix="/data", tags=["data"])
app.include_router(indemap.router)  # Indemap DB integration endpoints
app.include_router(dart_suggestion.router, prefix="/dart", tags=["dart_suggestion"])
app.include_router(extract_driver.router, prefix="/extract", tags=["extract_driver"])
app.include_router(doc_extraction.router, prefix="/doc", tags=["doc_extraction"])
app.include_router(extracts.router, prefix="/extracts", tags=["extracts"])
app.include_router(quality.router, prefix="/quality", tags=["quality"])
app.include_router(settings_router.router, prefix="/settings", tags=["settings"])
app.include_router(hl7.router, prefix="/hl7", tags=["hl7"])
