"""Tier 4: Real-World Scenarios — Scenario 4: Automated Monthly Billing Blast."""

import datetime
from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import MemberRepository
from wri_kas_bot.scheduler import execute_billing_blast


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
async def test_scenario_4_monthly_billing_cycle(
    initialized_db,
    mock_evolution_api,
):
    """Scenario 4: Monthly billing blast dispatches reminders and Alice pays."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "BLAST_ID"}})

    # 1-5. Run monthly billing blast for March 1, 2026
    target_date = datetime.date(2026, 3, 1)
    dispatched = await execute_billing_blast(
        db=db,
        evolution=evo,
        target_date=target_date,
        delay_between_messages=False,
    )
    assert dispatched == 3

    with db.connect() as conn:
        for m_id in (1, 2, 3):
            member = MemberRepository(conn).get_by_id(m_id)
            assert member.last_billed_at is not None

    # 6-7. Alice responds and pays Rp 20.000 for Feb and Mar dues
    pay_res = await handle_treasurer(
        _make_payload("/tunai 6281100000001 20000", "6281100000099"),
        db=db,
        evolution=evo,
    )
    assert pay_res["status"] == "cash_recorded"

    # Alice queries status: delinquent dues dropped from 11 to 9
    status_res = await handle_member(
        _make_payload("/status", "6281100000001"),
        db=db,
        evolution=evo,
    )
    assert status_res["status"] == "ok"
    assert status_res["unpaid_count"] == 9
    assert status_res["net_bill"] == 90000
