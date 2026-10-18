import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, services, ui
from .api import api
from .config import settings
from .db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("emr")


def _retry_once() -> None:
    db = SessionLocal()
    try:
        counts = services.retry_pending(db)
        if counts:
            log.info("retried pending verifications: %s", counts)
    except Exception:  # never let the retry loop die
        log.exception("retry of pending verifications failed")
    finally:
        db.close()


async def _retry_loop(interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(_retry_once)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        created = auth.ensure_users_from_env(db)
        if created:
            log.warning("created users: %s", ", ".join(created))
    finally:
        db.close()
    task = asyncio.create_task(_retry_loop(settings.retry_interval)) if settings.retry_interval > 0 else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="MoH Child EMR (demo)", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

secret_key = settings.secret_key
if not secret_key:
    log.warning("EMR_SECRET_KEY not set: using a random key, so sessions end on restart")
    secret_key = secrets.token_urlsafe(32)
auth.init_tokens(secret_key)

app.add_middleware(
    SessionMiddleware, secret_key=secret_key, session_cookie="emr_session", max_age=8 * 3600,
    same_site="lax", https_only=settings.secure_cookies,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
    return response


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.mount("/api/v1", api)
app.include_router(ui.router)


@app.exception_handler(auth.LoginRequired)
async def _login_required(request: Request, exc: auth.LoginRequired):
    return ui.login_redirect(request)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    user = None
    if request.session.get("user_id"):
        db = SessionLocal()
        try:
            user = auth.current_user(request, db)
        except auth.LoginRequired:
            pass
        finally:
            db.close()
    return ui.render(request, "error.html", user, status_code=exc.status_code, message=exc.detail,
                     code=exc.status_code)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}
