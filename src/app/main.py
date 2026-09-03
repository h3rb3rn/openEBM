"""
EBM Analyzer - FastAPI application entry point.
Sovereign AI stack | GDPR-compliant | air-gap ready
"""
import logging
import os
import sys
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .services.error_tracking import init_error_tracking
from .api import api_router
from .api.auth import get_current_user_optional, CurrentUser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
settings = get_settings()

# Must run before the FastAPI app is constructed so Sentry's ASGI
# middleware hook can wrap it. No-op if SENTRY_DSN is unset.
init_error_tracking()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("EBM Analyzer starting...")
    # Schema is managed by Alembic migrations, run once before uvicorn spawns
    # workers (see docker/app.Dockerfile CMD) — not here, since running
    # migrations from every worker process would race.
    try:
        await _seed_demo_tenant()
    except Exception as e:
        logger.error("Startup error: %s", e)

    yield

    logger.info("EBM Analyzer shutting down")


async def _seed_demo_tenant():
    """Create the demo tenant + admin user (idempotent, race-safe)."""
    from .database import AsyncSessionLocal
    from .models.base import Tenant, User, UserRole
    from .api.auth import hash_password
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    try:
        async with AsyncSessionLocal() as db:
            existing = await db.execute(select(Tenant).where(Tenant.slug == "demo"))
            if existing.scalar_one_or_none():
                return

            tenant = Tenant(name="Demo Praxis", slug="demo", settings={})
            db.add(tenant)
            await db.flush()

            admin = User(
                tenant_id=tenant.id,
                email="admin@demo.local",
                hashed_password=hash_password("demo1234"),
                full_name="Demo Administrator",
                role=UserRole.ADMIN,
            )
            db.add(admin)
            await db.commit()
            logger.info("Demo tenant + admin user created (admin@demo.local / demo1234)")
    except IntegrityError:
        logger.info("Demo tenant already exists (concurrent worker won the race)")


app = FastAPI(
    title="EBM Analyzer",
    description="Sovereign, GDPR-compliant EBM coding assistant",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

# Static files + templates
_base_dir = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(_base_dir, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_base_dir, "templates"))

# Prometheus metrics at /metrics — request counts/latencies/status codes.
# Unauthenticated at the ASGI level, same as a typical Prometheus scrape
# target; restrict access at the reverse proxy / network layer if exposed
# beyond the internal network.
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# API routers
app.include_router(api_router, prefix="/api")


# ─── Frontend routes ─────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is not None:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/analyse", response_class=HTMLResponse)
async def analyse_page(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "analysis.html")


@app.get("/profil", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "profile.html")


@app.get("/patienten", response_class=HTMLResponse)
async def patients_page(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "patients.html")


@app.get("/fallakten", response_class=HTMLResponse)
async def cases_page(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "cases.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if user.role != "admin":
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "admin.html")


# ─── Health / system endpoints ───────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ebm-app", "version": "1.0.0"}


@app.get("/api/system/status")
async def system_status():
    """Probe all backend services and report an aggregated status."""
    from .services.mcp_client import MCPClient
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from sqlalchemy import text

    results = {}

    # PostgreSQL
    try:
        from .database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        results["postgres"] = "ok"
    except Exception as e:
        results["postgres"] = f"fehler: {e}"

    # Valkey
    try:
        valkey = aioredis.from_url(settings.valkey_url)
        await valkey.ping()
        results["valkey"] = "ok"
        await valkey.aclose()
    except Exception as e:
        results["valkey"] = f"fehler: {e}"

    # ChromaDB
    try:
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(
                chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                chroma_client_auth_credentials=settings.chroma_token,
            ),
        )
        client.heartbeat()
        results["chromadb"] = "ok"
    except Exception as e:
        results["chromadb"] = f"fehler: {e}"

    # MCP server
    mcp = MCPClient()
    results["mcp_server"] = "ok" if await mcp.health_check() else "nicht erreichbar"

    all_ok = all(v == "ok" for v in results.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degradiert", "services": results},
        status_code=200 if all_ok else 207,
    )


# ─── Exception handlers ──────────────────────────────────────────────

@app.exception_handler(SQLAlchemyError)
async def db_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error("Database error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Datenbankfehler. Bitte erneut versuchen."},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Nicht gefunden"})
    return RedirectResponse(url="/dashboard")
