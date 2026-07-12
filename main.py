from fastapi import FastAPI, Request, Depends, Form, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import secrets
import hashlib
import time
import json

from app.config import settings
from app.database import check_db_connection
from app.routers import (
    auth, patients, diagnostics, appointments,
    notifications, admin, ml_proxy
)
from app.routers import se
from app.routers import doctors, profile   # ← NEW

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("OmniSensus.Backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting OmniSensus Backend...")
    db_ok = await check_db_connection()
    if db_ok:
        logger.info("Neon PostgreSQL connected")
    else:
        logger.warning("Database connection failed - check DATABASE_URL")
    yield
    logger.info("Shutting down OmniSensus Backend...")


# ── DOCS SESSION ──────────────────────────────────────────────────────────
DOCS_SESSION_COOKIE  = "omni_docs_session"
DOCS_SESSION_MAX_AGE = 7200  # 2 hours


def _make_session_token() -> str:
    window = int(time.time()) // DOCS_SESSION_MAX_AGE
    raw = f"{settings.DOCS_USERNAME}:{settings.DOCS_PASSWORD}:{settings.JWT_SECRET}:{window}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_docs_session_valid(token: str) -> bool:
    return secrets.compare_digest(token, _make_session_token())


def _login_page(error: bool = False) -> str:
    error_html = """
        <div class="error">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            Invalid username or password.
        </div>
    """ if error else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>OmniSensus - API Docs Login</title>
    <style>
        *{{box-sizing:border-box;margin:0;padding:0}}
        body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
              background:#0f1117;min-height:100vh;display:flex;
              align-items:center;justify-content:center}}
        .card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:12px;
               padding:40px;width:100%;max-width:400px;
               box-shadow:0 20px 60px rgba(0,0,0,0.5)}}
        .logo{{display:flex;align-items:center;gap:10px;margin-bottom:28px}}
        .logo-icon{{width:36px;height:36px;background:#10847E;border-radius:8px;
                    display:flex;align-items:center;justify-content:center;
                    font-weight:700;color:white;font-size:16px}}
        .logo-text{{color:#e2e8f0;font-size:18px;font-weight:600}}
        h2{{color:#e2e8f0;font-size:20px;font-weight:600;margin-bottom:6px}}
        p{{color:#64748b;font-size:14px;margin-bottom:16px}}
        label{{display:block;color:#94a3b8;font-size:13px;
               font-weight:500;margin-bottom:6px}}
        input{{width:100%;padding:10px 14px;background:#0f1117;
               border:1px solid #2a2d3a;border-radius:8px;color:#e2e8f0;
               font-size:14px;outline:none;margin-bottom:18px}}
        input:focus{{border-color:#10847E}}
        button{{width:100%;padding:11px;background:#10847E;color:white;
                border:none;border-radius:8px;font-size:14px;
                font-weight:600;cursor:pointer}}
        .error{{display:flex;align-items:center;gap:8px;
                background:rgba(220,38,38,0.1);border:1px solid rgba(220,38,38,0.3);
                color:#f87171;font-size:13px;padding:10px 14px;
                border-radius:8px;margin-bottom:20px}}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <div class="logo-icon">O</div>
            <div><div class="logo-text">OmniSensus</div></div>
        </div>
        <h2>API Documentation</h2>
        <p>Sign in with developer credentials. Session expires in 2 hours.</p>
        {error_html}
        <form method="post" action="/docs/login">
            <label>Username</label>
            <input type="text" name="username" required autofocus>
            <label>Password</label>
            <input type="password" name="password" required>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>"""


# ── APP ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title        = settings.APP_NAME,
    version      = settings.APP_VERSION,
    description  = "## OmniSensus Medical Platform - Backend API",
    docs_url     = None,
    redoc_url    = None,
    openapi_url  = None,
    lifespan     = lifespan,
    openapi_tags = [
        {"name": "Health",            "description": "Service health check"},
        {"name": "Authentication",    "description": "Login, logout, token refresh"},
        {"name": "Patients",          "description": "Patient profiles, history, vitals, medications"},
        {"name": "Doctors",           "description": "Doctor profiles, availability, workload"},
        {"name": "Profile",           "description": "Profile update, password change, preferences"},
        {"name": "Diagnostics",       "description": "ML diagnostics, readmission risk"},
        {"name": "Appointments",      "description": "Book and manage appointments"},
        {"name": "Notifications",     "description": "User notifications"},
        {"name": "Admin",             "description": "Analytics, user management, resources, audit"},
        {"name": "ML Proxy",          "description": "Chat, Q&A, reports, medications"},
        {"name": "Support Engineers", "description": "SE portal - model monitoring, dev reports"},
    ],
)

# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://omnisensus.tech",
        "https://omnisensus.netlify.app",
    ],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── DOCS ROUTES ───────────────────────────────────────────────────────────
@app.get("/docs", include_in_schema=False)
async def docs_ui(omni_docs_session: str = Cookie(default=None)):
    if omni_docs_session and _is_docs_session_valid(omni_docs_session):
        schema      = get_openapi(title=app.title, version=app.version,
                                  description=app.description, routes=app.routes)
        schema_json = json.dumps(schema)
        html = f"""<!DOCTYPE html>
<html><head><title>{settings.APP_NAME} - API Docs</title>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head><body><div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({{spec:{schema_json},dom_id:'#swagger-ui',
presets:[SwaggerUIBundle.presets.apis],deepLinking:true,persistAuthorization:true}});
</script>
<style>.topbar{{background:#10847E!important}}</style>
</body></html>"""
        return HTMLResponse(html)
    return HTMLResponse(_login_page(), status_code=200)


@app.post("/docs/login", include_in_schema=False)
async def docs_login(username: str = Form(...), password: str = Form(...)):
    valid_user = secrets.compare_digest(username.encode(), settings.DOCS_USERNAME.encode())
    valid_pass = secrets.compare_digest(password.encode(), settings.DOCS_PASSWORD.encode())
    if valid_user and valid_pass:
        response = RedirectResponse(url="/docs", status_code=302)
        response.set_cookie(key=DOCS_SESSION_COOKIE, value=_make_session_token(),
                            httponly=True, secure=True, samesite="strict",
                            max_age=DOCS_SESSION_MAX_AGE)
        return response
    return HTMLResponse(_login_page(error=True), status_code=401)


@app.get("/docs/logout", include_in_schema=False)
async def docs_logout():
    response = RedirectResponse(url="/docs", status_code=302)
    response.delete_cookie(DOCS_SESSION_COOKIE)
    return response


# ── ROUTERS ───────────────────────────────────────────────────────────────
PREFIX = "/api/v1"
app.include_router(auth.router,          prefix=PREFIX)
app.include_router(patients.router,      prefix=PREFIX)
app.include_router(doctors.router,       prefix=PREFIX)   # NEW
app.include_router(profile.router,       prefix=PREFIX)   # NEW
app.include_router(diagnostics.router,   prefix=PREFIX)
app.include_router(appointments.router,  prefix=PREFIX)
app.include_router(notifications.router, prefix=PREFIX)
app.include_router(admin.router,         prefix=PREFIX)
app.include_router(ml_proxy.router,      prefix=PREFIX)
app.include_router(se.router,            prefix=PREFIX)


# ── HEALTH ────────────────────────────────────────────────────────────────
@app.get("/api/v1/health", tags=["Health"])
async def health():
    db_ok = await check_db_connection()
    return {
        "status":       "success",
        "service":      settings.APP_NAME,
        "version":      settings.APP_VERSION,
        "env":          settings.ENV,
        "db_connected": db_ok,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


# ── ERROR HANDLERS ────────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(status_code=404,
                        content={"status": "error", "message": "Endpoint not found."})


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.error(f"500: {exc}")
    return JSONResponse(status_code=500,
                        content={"status": "error", "message": "Internal server error."})


if __name__ == "__main__":
    import uvicorn, os
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)