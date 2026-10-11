"""Tier 3: Cross-Feature Interactions: Member Status Query Reflection."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import TransactionRepository


def _make_payload(text: str, phone: str) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": f"{phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_1",
                },
                "message": {"conversation": text},
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_flow_member_status_reflects_pending_transaction(initialized_db, mock_evolution_api):
    """Pairwise: Member sends receipt -> Query /status shows 'Menunggu verifikasi bendahara'."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_repo.create_draft(member_id=1, amount_paid=20000, ocr_amount=20000)

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_ID"}})

    await handle_member(_make_payload("/status", "6281100000001"), db=db, evolution=evo)
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "menunggu verifikasi bendahara" in msg_text.lower()


@pytest.mark.asyncio
async def test_flow_member_status_reflects_approval_and_paid_months(
    initialized_db, mock_evolution_api
):
    """Pairwise: Treasurer approves -> Query /status reflects cleared months and 0 net bill."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_ID"}})

    # Pay all 110,000 dues via cash
    await handle_treasurer(
        _make_payload("/tunai 6281100000001 110000", "6281100000099"),
        db=db,
        evolution=evo,
    )

    await handle_member(_make_payload("/status", "6281100000001"), db=db, evolution=evo)
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "Lunas semua!" in msg_text
    assert "*Total Tagihan Bersih:* Rp 0" in msg_text


@pytest.mark.asyncio
async def test_flow_member_status_reflects_undo_rollback(initialized_db, mock_evolution_api):
    """Pairwise: Treasurer undoes payment -> Query /status reflects reopened unpaid dues."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_ID"}})

    # Pay dues
    await handle_treasurer(
        _make_payload("/tunai 6281100000001 20000", "6281100000099"),
        db=db,
        evolution=evo,
    )
    # Undo dues
    await handle_treasurer(_make_payload("/undo", "6281100000099"), db=db, evolution=evo)

    # Check status
    await handle_member(_make_payload("/status", "6281100000001"), db=db, evolution=evo)
    msg_text = evo.send_text.call_args.kwargs["text"]
    assert "11 bln" in msg_text
    assert "*Total Tagihan Bersih:* Rp 110,000" in msg_text
