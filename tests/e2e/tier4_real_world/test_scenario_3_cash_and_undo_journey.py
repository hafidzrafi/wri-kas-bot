"""Tier 4: Real-World Scenarios: Scenario 3: Cash Payment & Undo Rollback (Charlie)."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def _make_payload(text: str, phone: str) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": f"{phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_CMD",
                },
                "message": {"conversation": text},
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_scenario_3_charlie_cash_and_undo(
    initialized_db,
    mock_evolution_api,
    mock_sheets_client,
    mock_drive_client,
):
    """Scenario 3: Cash payment followed by undo rollback and sheets cell clearing."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_ID"}})

    # 1-3. Record Rp 20.000 cash for Charlie (clears Feb & Mar)
    cash_res = await handle_treasurer(
        _make_payload("/tunai 6281100000003 20000", "6281100000099"),
        db=db,
        evolution=evo,
    )
    assert cash_res["status"] == "cash_recorded"
    tx_id = cash_res["transaction_id"]

    # 4. Outbox worker syncs 'Tunai' into Sheets
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    await worker.run_outbox_cycle()
    assert len(mock_sheets_client.updated_cells) == 2

    # 5-7. Treasurer issues /undo
    undo_res = await handle_treasurer(
        _make_payload("/undo", "6281100000099"),
        db=db,
        evolution=evo,
    )
    assert undo_res["status"] == "undone"

    # 8. Outbox executes Google Sheets batchClear
    await worker.run_outbox_cycle()

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REVERTED"
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=3, year=2026)
        assert len(paid_dues) == 0

    # 9. Charlie checks /status
    status_res = await handle_member(
        _make_payload("/status", "6281100000003"),
        db=db,
        evolution=evo,
    )
    assert status_res["status"] == "ok"
    assert status_res["unpaid_count"] == 11
    assert status_res["net_bill"] == 110000
