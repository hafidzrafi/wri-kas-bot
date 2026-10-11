"""Tier 1: Feature Coverage — Feature 16: Member Self-Service Handler."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import TransactionRepository


def _make_member_payload(
    text: str,
    sender_phone: str = "6281100000001",
) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": f"{sender_phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_MEMBER_1",
                },
                "message": {"conversation": text},
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_member_status_command(initialized_db, mock_evolution_api):
    """Verify /status returns member dues card with balance and unpaid months."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_member_payload("/status")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    assert res["unpaid_count"] == 11  # Feb-Dec 2026
    assert res["net_bill"] == 110000

    evo.send_text.assert_awaited_once()
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "STATUS KAS WRI" in msg_text
    assert "Alice" in msg_text
    assert "Rp 110,000" in msg_text


@pytest.mark.asyncio
async def test_member_status_indicates_pending_transaction(initialized_db, mock_evolution_api):
    """Verify /status includes pending notice when transaction awaits treasurer verification."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_repo.create_draft(
            member_id=1,
            amount_paid=20000,
            ocr_amount=20000,
        )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_member_payload("/status")
    await handle_member(payload, db=db, evolution=evo)

    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "menunggu verifikasi" in msg_text.lower()
    assert "Rp 20,000" in msg_text


@pytest.mark.asyncio
async def test_member_bayar_command(initialized_db, mock_evolution_api):
    """Verify /bayar returns payment details (Bank Jago account number) and net bill."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_member_payload("/bayar")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "PEMBAYARAN KAS WRI" in msg_text
    assert "Bank Jago" in msg_text
    assert "QRIS" in msg_text


@pytest.mark.asyncio
async def test_member_help_command(mock_evolution_api, initialized_db):
    """Verify /help responds with available member commands."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_member_payload("/help")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "BANTUAN BOT KAS WRI" in msg_text
    assert "/status" in msg_text
    assert "/bayar" in msg_text


@pytest.mark.asyncio
async def test_non_member_command_rejection(initialized_db, mock_evolution_api):
    """Verify unregistered numbers receive a polite notice."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_member_payload("/status", sender_phone="6289999999999")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "unregistered"
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "belum terdaftar" in msg_text.lower()
