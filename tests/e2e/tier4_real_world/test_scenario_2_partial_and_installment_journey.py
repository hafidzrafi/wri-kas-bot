"""Tier 4: Real-World Scenarios: Scenario 2: Partial Underpayment & Installment Top-up (Bob)."""

from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository


def _make_treasurer_payload(text: str) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": "6281100000099@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_CMD",
                },
                "message": {"conversation": text},
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_scenario_2_bob_underpays_and_tops_up(
    initialized_db,
    mock_evolution_api,
    mock_drive_client,
    mock_sheets_client,
):
    """Scenario 2: Underpayment into deposit balance followed by top-up clearing 2 months."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_ID"}})

    # 1. Bob pays Rp 5.000 cash (< Rp 10.000 minimum due)
    res1 = await handle_treasurer(
        _make_treasurer_payload("/tunai 6281100000002 5000"),
        db=db,
        evolution=evo,
    )
    assert res1["status"] == "cash_recorded"

    with db.connect() as conn:
        bob = MemberRepository(conn).get_by_id(2)
        assert bob.balance == 5000
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=2, year=2026)
        assert len(paid_dues) == 0  # 0 months cleared yet

    # 2. Bob tops up with Rp 15.000 cash
    res2 = await handle_treasurer(
        _make_treasurer_payload("/tunai 6281100000002 15000"),
        db=db,
        evolution=evo,
    )
    assert res2["status"] == "cash_recorded"

    # Pool = 5000 + 15000 = 20000 -> clears Feb and Mar
    with db.connect() as conn:
        bob = MemberRepository(conn).get_by_id(2)
        assert bob.balance == 0
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=2, year=2026)
        assert len(paid_dues) == 2
        assert [d.month for d in paid_dues] == ["Februari", "Maret"]

    # 3. Outbox worker syncs to sheets
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1
    assert len(mock_sheets_client.updated_cells) == 2
