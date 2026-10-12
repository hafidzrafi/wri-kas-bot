"""FastAPI web server and webhook dispatcher for Kas WRI Bot.

Exposes /health monitoring endpoint and /webhook for Evolution API v2
events with automatic validation and handler dispatching.
"""

import asyncio
import logging
import random
import time
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

_PROCESSED_MESSAGE_IDS: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 120.0


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

    # Initial member synchronization from Google Sheets
    try:
        from wri_kas_bot.sync import sync_members_from_sheets

        synced = sync_members_from_sheets(db)
        logger.info("Initial member sync completed: %d members", synced)
    except Exception as exc:
        logger.warning("Initial member sync skipped or failed during startup: %s", exc)

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
@app.post("/webhook/wri", status_code=status.HTTP_200_OK)
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

    # 3. Webhook idempotency guard (ignore duplicate message deliveries)
    if payload.message_id:
        now_ts = time.time()
        expired = [
            mid for mid, ts in _PROCESSED_MESSAGE_IDS.items() if now_ts - ts > _DEDUP_TTL_SECONDS
        ]
        for mid in expired:
            _PROCESSED_MESSAGE_IDS.pop(mid, None)

        if payload.message_id in _PROCESSED_MESSAGE_IDS:
            logger.info("Ignoring duplicate webhook for message_id=%s", payload.message_id)
            return JSONResponse(
                status_code=status.HTTP_200_OK, content={"status": "ignored_duplicate"}
            )
        _PROCESSED_MESSAGE_IDS[payload.message_id] = now_ts

    db = get_db(request)
    evolution = get_evolution(request)

    sender_phone = payload.sender_phone
    sender_jid = payload.sender_jid
    text_body = payload.text_body.strip()

    # UX Feature: Mark as read (blue ticks) & send typing presence indicator
    if sender_jid:
        if payload.message_id:
            await evolution.mark_message_as_read(
                remote_jid=sender_jid,
                message_id=payload.message_id,
                from_me=False,
            )

        words = len(text_body.split()) if text_body else 1
        jitter = random.randint(-150, 250)
        delay_ms = max(500, settings.typing_delay_base_ms + (words * 30) + jitter)
        await evolution.send_presence(
            remote_jid=sender_jid,
            presence="composing",
            delay_ms=delay_ms,
        )
        if settings.typing_delay_enabled and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

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
