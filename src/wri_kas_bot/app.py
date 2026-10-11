"""FastAPI web server and webhook dispatcher for Kas WRI Bot.

Exposes /health monitoring endpoint and /webhook for Evolution API v2
events with automatic validation and handler dispatching.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.handlers.receipt_handler import handle_receipt
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Application lifespan context manager for startup and shutdown routines."""
    settings = get_settings()
    db_path = settings.database_url
    if "sqlite:///" in db_path:
        db_path = db_path.split("sqlite:///")[-1]
    elif "sqlite+aiosqlite:///" in db_path:
        db_path = db_path.split("sqlite+aiosqlite:///")[-1]

    db = Database(db_path)
    db.init_schema()
    app_instance.state.db = db
    app_instance.state.evolution = EvolutionClient()
    logger.info("wri-kas-bot started up with db=%s", db_path)
    yield
    logger.info("wri-kas-bot shutting down")


app = FastAPI(
    title="wri-kas-bot",
    version="0.1.0",
    description="WhatsApp Dues Automation Bot for Workshop Riset Informatika",
    lifespan=lifespan,
)


def get_db(request: Request) -> Database:
    """Retrieve shared Database instance from app state or create fallback."""
    if hasattr(request.app.state, "db") and request.app.state.db is not None:
        return request.app.state.db
    settings = get_settings()
    db_path = settings.database_url
    if "sqlite" in db_path:
        db_path = db_path.split(":///")[-1]
    db = Database(db_path)
    db.init_schema()
    return db


def get_evolution(request: Request) -> EvolutionClient:
    """Retrieve shared EvolutionClient instance from app state or create fallback."""
    if hasattr(request.app.state, "evolution") and request.app.state.evolution is not None:
        return request.app.state.evolution
    return EvolutionClient()


@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> dict[str, str]:
    """Health check endpoint for container probes."""
    return {"status": "ok", "service": "wri-kas-bot", "version": "0.1.0"}


@app.post("/webhook", status_code=status.HTTP_200_OK)
async def webhook_handler(request: Request) -> JSONResponse:
    """Inbound webhook event receiver for Evolution API v2."""
    settings = get_settings()
    if settings.webhook_secret:
        header_secret = request.headers.get("x-webhook-secret") or request.headers.get("apikey")
        if header_secret != settings.webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing webhook secret header",
            )

    try:
        raw_json = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid JSON"
        ) from None

    # If payload is missing essential Evolution API structure
    if not isinstance(raw_json, dict) or "data" not in raw_json:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored_unknown"})

    try:
        payload = WebhookPayload.model_validate(raw_json)
    except Exception as exc:
        logger.warning("Failed validating webhook payload: %s", exc)
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored_malformed"})

    # 1. Ignore Outbound Bot Messages
    if payload.is_from_me:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored_from_me"})

    # 2. Ignore WhatsApp Groups
    if payload.is_group:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored_group"})

    db = get_db(request)
    evolution = get_evolution(request)

    sender_phone = payload.sender_phone
    sender_jid = payload.sender_jid
    text_body = payload.text_body.strip()

    is_treasurer = sender_phone == settings.bendahara_number or sender_jid == settings.bendahara_jid

    treasurer_cmds = (
        "/acc",
        "/approve",
        "/reject",
        "/tolak",
        "/tunai",
        "/cash",
        "/undo",
        "/koreksi",
        "/tambahsaldo",
        "/setsaldo",
    )

    try:
        # Route 1: Treasurer Moderation Command
        if is_treasurer and any(text_body.lower().startswith(c) for c in treasurer_cmds):
            res = await handle_treasurer(payload, db, evolution)
            return JSONResponse(status_code=status.HTTP_200_OK, content=res)

        # Route 2: Receipt Image Ingestion
        if payload.has_media:
            res = await handle_receipt(payload, db, evolution)
            return JSONResponse(status_code=status.HTTP_200_OK, content=res)

        # Route 3: Member Text Commands
        if text_body:
            res = await handle_member(payload, db, evolution)
            return JSONResponse(status_code=status.HTTP_200_OK, content=res)

    except Exception as exc:
        logger.error("Unhandled error processing webhook: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "error", "detail": str(exc)},
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored_no_action"})
