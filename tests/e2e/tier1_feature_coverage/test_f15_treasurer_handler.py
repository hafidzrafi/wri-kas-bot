"""Tier 1: Feature Coverage: Feature 15: Treasurer Moderation Handler."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def _make_treasurer_payload(
    text: str,
    sender_phone: str = "6281100000099",
    quoted_id: str | None = None,
) -> WebhookPayload:
    quoted_ctx = {}
    if quoted_id:
        quoted_ctx = {
            "extendedTextMessage": {
                "text": text,
                "contextInfo": {
                    "stanzaId": quoted_id,
                    "quotedMessage": {"conversation": "Bukti transfer"},
                },
            }
        }
    else:
        quoted_ctx = {"conversation": text}

    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": f"{sender_phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_CMD_1",
                },
                "message": quoted_ctx,
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_treasurer_acc_default_nominal(initialized_db, mock_evolution_api):
    """Verify swipe-reply /acc uses ocr_amount to approve and allocate dues."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(
            member_id=1,
            amount_paid=20000,
            payment_method="TRANSFER_JAGO",
            ocr_amount=20000,
            ocr_bank="Bank Jago",
        )
        tx_repo.set_notification_msg_id(tx_id, "NOTIF_MSG_123")

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_treasurer_payload(text="/acc", quoted_id="NOTIF_MSG_123")
    res = await handle_treasurer(payload, db=db, evolution=evo)

    assert res["status"] == "approved"
    assert res["transaction_id"] == tx_id

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        assert tx.balance_after == 0

        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 2
        assert [d.month for d in dues] == ["Februari", "Maret"]


@pytest.mark.asyncio
async def test_treasurer_acc_explicit_nominal(initialized_db, mock_evolution_api):
    """Verify /acc with explicit nominal overrides OCR detection."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(
            member_id=1,
            amount_paid=10000,
            payment_method="TRANSFER_JAGO",
            ocr_amount=10000,
            ocr_bank="Bank Jago",
        )
        tx_repo.set_notification_msg_id(tx_id, "NOTIF_MSG_EXPLICIT")

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_treasurer_payload(text="/acc 30000", quoted_id="NOTIF_MSG_EXPLICIT")
    res = await handle_treasurer(payload, db=db, evolution=evo)

    assert res["status"] == "approved"

    with db.connect() as conn:
        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 3
        assert [d.month for d in dues] == ["Februari", "Maret", "April"]


@pytest.mark.asyncio
async def test_treasurer_reject_reason(initialized_db, mock_evolution_api):
    """Verify /reject sets transaction status to REJECTED and notifies member with reason."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(
            member_id=1,
            amount_paid=10000,
            ocr_amount=10000,
        )
        tx_repo.set_notification_msg_id(tx_id, "NOTIF_MSG_REJECT")

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_treasurer_payload(
        text="/reject Bukti transfer buram", quoted_id="NOTIF_MSG_REJECT"
    )
    res = await handle_treasurer(payload, db=db, evolution=evo)

    assert res["status"] == "rejected"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REJECTED"
        assert tx.rejection_reason == "Bukti transfer buram"

        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 0


@pytest.mark.asyncio
async def test_treasurer_tunai_cash_payment(initialized_db, mock_evolution_api):
    """Verify /tunai command allocates cash dues without requiring receipt image."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    payload = _make_treasurer_payload(text="/tunai 6281100000001 20000")
    res = await handle_treasurer(payload, db=db, evolution=evo)

    assert res["status"] == "cash_recorded"
    tx_id = res["transaction_id"]

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        assert tx.payment_method == "CASH"

        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 2
        assert [d.month for d in dues] == ["Februari", "Maret"]


@pytest.mark.asyncio
async def test_treasurer_undo_reverts_transaction(initialized_db, mock_evolution_api):
    """Verify /undo on approved transaction reverts balance and dues."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    # First allocate cash dues
    cash_payload = _make_treasurer_payload(text="/tunai 6281100000001 20000")
    cash_res = await handle_treasurer(cash_payload, db=db, evolution=evo)
    tx_id = cash_res["transaction_id"]

    # Now issue /undo
    undo_payload = _make_treasurer_payload(text="/undo")
    undo_res = await handle_treasurer(undo_payload, db=db, evolution=evo)

    assert undo_res["status"] == "undone"
    assert undo_res["transaction_id"] == tx_id

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REVERTED"

        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 0


@pytest.mark.asyncio
async def test_treasurer_auth_rejection(initialized_db):
    """Verify non-treasurer JID is unauthorized to issue moderation commands."""
    db = Database(initialized_db)
    # Alice sends treasurer moderation command
    payload = _make_treasurer_payload(text="/acc", sender_phone="6281100000001")
    res = await handle_treasurer(payload, db=db)

    assert res["status"] == "unauthorized"
