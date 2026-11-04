"""Local dashboard. Real filesystem targets are configured through the CLI only."""

import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .demo import seed_demo
from .reports import html_report
from .storage import Store


def create_app(db_path: Path | None = None) -> FastAPI:
    store = Store(db_path or Path(os.environ.get("RECOVERY_DB", "data/runs.db")))
    app = FastAPI(title="Recovery Assurance", version="0.1.0", docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")
    demo_lock = threading.Lock()

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/runs")
    def runs():
        return store.list()

    @app.post("/api/demo", status_code=201)
    def demo(request: Request):
        if request.headers.get("X-Recovery-Action") != "demo" or request.headers.get(
            "origin", str(request.base_url).rstrip("/")
        ) != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Use the local dashboard to run sample drills")
        if not demo_lock.acquire(blocking=False):
            raise HTTPException(409, "A sample drill set is already running")
        try:
            return seed_demo(store)
        finally:
            demo_lock.release()

    def get_report(run_id):
        report = store.get(run_id)
        if report is None:
            raise HTTPException(404, "Run not found")
        return report

    @app.get("/api/runs/{run_id}/report.json")
    def report_json(run_id: str):
        report = get_report(run_id)
        return JSONResponse(
            report,
            headers={
                "Content-Disposition": f'attachment; filename="recovery-{report["id"]}.json"',
            },
        )

    @app.get("/api/runs/{run_id}/report.html")
    def report_html(run_id: str):
        report = get_report(run_id)
        return HTMLResponse(
            html_report(report),
            headers={
                "Content-Disposition": f'attachment; filename="recovery-{report["id"]}.html"',
            },
        )

    return app
