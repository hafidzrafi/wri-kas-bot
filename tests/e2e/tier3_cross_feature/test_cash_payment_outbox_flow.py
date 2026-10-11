"""Tier 3: Cross-Feature Interactions: Cash Payment and Google Sheets Sync."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MonthlyDuesRepository


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
async def test_flow_cash_payment_allocates_and_syncs_tunai(
    initialized_db, mock_sheets_client, mock_drive_client
):
    """Pairwise: /tunai command runs FIFO, skips Drive upload, and writes 'Tunai' into Sheets."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    # 1. Execute /tunai
    payload = _make_treasurer_payload("/tunai 6281100000001 20000")
    res = await handle_treasurer(payload, db=db, evolution=evo)
    assert res["status"] == "cash_recorded"

    # 2. Run Outbox Sync
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1

    # Verify 'Tunai' written to sheets and zero drive uploads occurred
    assert len(mock_sheets_client.updated_cells) == 2
    for cell in mock_sheets_client.updated_cells:
        assert cell["proof_url"] == "Tunai"
    assert len(mock_drive_client.uploaded_files) == 0


@pytest.mark.asyncio
async def test_flow_cash_payment_undo_restores_unpaid(
    initialized_db, mock_sheets_client, mock_drive_client
):
    """Pairwise: /undo on cash payment clears 'Tunai' cell and reverts dues to UNPAID."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_MSG_ID"}})

    # Record cash dues
    await handle_treasurer(
        _make_treasurer_payload("/tunai 6281100000001 20000"),
        db=db,
        evolution=evo,
    )
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    await worker.run_outbox_cycle()

    # Issue /undo
    undo_res = await handle_treasurer(_make_treasurer_payload("/undo"), db=db, evolution=evo)
    assert undo_res["status"] == "undone"

    # Run reverse sync
    await worker.run_outbox_cycle()

    with db.connect() as conn:
        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 0
