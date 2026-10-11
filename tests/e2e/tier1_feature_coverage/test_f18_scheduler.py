"""Tier 1: Feature Coverage: Feature 18: Monthly Billing Blast (scheduler.py)."""

import datetime
from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.repository import MemberRepository
from wri_kas_bot.scheduler import execute_billing_blast, get_random_jitter


@pytest.mark.asyncio
async def test_scheduler_identifies_unpaid_members(initialized_db):
    """Verify scheduler targets active members with unpaid dues up to current month."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "BLAST_MSG_ID"}})

    target_date = datetime.date(2026, 3, 1)
    dispatched = await execute_billing_blast(
        db=db,
        evolution=evo,
        target_date=target_date,
        delay_between_messages=False,
    )

    assert dispatched == 3
    assert evo.send_text.await_count == 3


@pytest.mark.asyncio
async def test_scheduler_net_bill_deducts_balance(initialized_db):
    """Verify net bill calculation formula: max(0, unpaid_count * 10000 - balance)."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        # Give Alice 5000 balance
        MemberRepository(conn).update_balance(1, 5000)

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "BLAST_MSG_ID"}})

    target_date = datetime.date(2026, 3, 1)  # 2 eligible unpaid months (Feb, Mar)
    await execute_billing_blast(
        db=db,
        evolution=evo,
        target_date=target_date,
        delay_between_messages=False,
    )

    alice_call = [
        c for c in evo.send_text.call_args_list if "6281100000001" in c.kwargs["recipient"]
    ][0]
    msg_text = alice_call.kwargs["text"]
    # 2 months * 10000 - 5000 balance = 15,000
    assert "Rp 15,000" in msg_text


@pytest.mark.asyncio
async def test_scheduler_updates_last_billed_at(initialized_db):
    """Verify scheduler persists last_billed_at timestamp for dispatched member."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "BLAST_MSG_ID"}})

    target_date = datetime.date(2026, 3, 1)
    await execute_billing_blast(
        db=db,
        evolution=evo,
        target_date=target_date,
        delay_between_messages=False,
    )

    with db.connect() as conn:
        alice = MemberRepository(conn).get_by_id(1)
        assert alice.last_billed_at is not None


@pytest.mark.asyncio
async def test_scheduler_skips_already_billed_members(initialized_db):
    """Verify members billed in the same month cycle are excluded upon restart."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "BLAST_MSG_ID"}})

    target_date = datetime.date(2026, 3, 1)
    # 1st blast
    count1 = await execute_billing_blast(
        db=db,
        evolution=evo,
        target_date=target_date,
        delay_between_messages=False,
    )
    assert count1 == 3

    # 2nd blast on same cycle
    count2 = await execute_billing_blast(
        db=db,
        evolution=evo,
        target_date=target_date,
        delay_between_messages=False,
    )
    assert count2 == 0


def test_scheduler_jitter_delay_range():
    """Verify anti-spam jitter delay falls within 30-60 seconds."""
    for _ in range(20):
        jitter = get_random_jitter()
        assert 30.0 <= jitter <= 60.0
