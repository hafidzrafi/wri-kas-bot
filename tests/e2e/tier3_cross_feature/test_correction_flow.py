"""Tier 3: Cross-Feature Interactions — Transaction Correction Flow (/koreksi)."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def _make_treasurer_payload(text: str) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": "6281100000099@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_CMD_1",
                },
                "message": {"conversation": text},
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_flow_correction_rolls_back_and_reallocates(initialized_db, mock_sheets_client):
    """Pairwise: /koreksi rolls back previous allocation atomically and re-allocates new nominal."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    # 1. First record cash dues of 10,000 (1 month)
    await handle_treasurer(
        _make_treasurer_payload("/tunai 6281100000001 10000"),
        db=db,
        evolution=evo,
    )

    with db.connect() as conn:
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 1
        assert paid_dues[0].month == "Februari"

    # 2. Correct to 25,000 (2 months + 5000 deposit)
    koreksi_res = await handle_treasurer(
        _make_treasurer_payload("/koreksi 25000"),
        db=db,
        evolution=evo,
    )
    assert koreksi_res["status"] == "koreksi_success"

    with db.connect() as conn:
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 2
        assert [d.month for d in paid_dues] == ["Februari", "Maret"]

        tx = TransactionRepository(conn).get_by_id(koreksi_res["transaction_id"])
        assert tx.status == "APPROVED"
        assert tx.amount_paid == 25000
        assert tx.balance_after == 5000


@pytest.mark.asyncio
async def test_flow_correction_updates_outbox_tasks(
    initialized_db, mock_sheets_client, mock_drive_client
):
    """Pairwise: Corrected transaction syncs new months to Google Sheets."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    # Initial payment of 10,000
    await handle_treasurer(
        _make_treasurer_payload("/tunai 6281100000001 10000"),
        db=db,
        evolution=evo,
    )
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    await worker.run_outbox_cycle()

    # Koreksi to 20,000
    await handle_treasurer(_make_treasurer_payload("/koreksi 20000"), db=db, evolution=evo)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(1)
        assert tx.sync_status == "SYNCED"
