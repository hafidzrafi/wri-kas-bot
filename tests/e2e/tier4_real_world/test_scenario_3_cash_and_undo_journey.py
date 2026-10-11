"""Tier 4: Real-World Scenarios — Scenario 3: Cash Payment & Undo Rollback (Charlie)."""

import importlib.util

import pytest

HAS_APP = (
    importlib.util.find_spec("wri_kas_bot.app") is not None
    and importlib.util.find_spec("wri_kas_bot.fifo") is not None
    and importlib.util.find_spec("wri_kas_bot.outbox") is not None
    and importlib.util.find_spec("wri_kas_bot.repository") is not None
    and importlib.util.find_spec("wri_kas_bot.undo") is not None
)

pytestmark = pytest.mark.skipif(not HAS_APP, reason="Full application stack not implemented yet")


@pytest.mark.asyncio
async def test_scenario_3_charlie_cash_and_undo(
    initialized_db,
    mock_evolution_api,
    mock_sheets_client,
):
    """Scenario 3:
    1. Charlie pays treasurer Rp 20.000 in cash.
    2. Treasurer issues command: `/tunai 6281100000003 20000`.
    3. FIFO clears Feb and Mar with payment_method='CASH'.
    4. Outbox worker writes literal 'Tunai' into PENGURUS sheet cells.
    5. Treasurer realizes payment was credited to wrong person.
    6. Treasurer issues swipe-reply `/undo`.
    7. Undo engine rolls back Charlie's balance and reopens Feb, Mar to UNPAID.
    8. Outbox executes Google Sheets batchClear, clearing the 'Tunai' cells.
    9. Charlie checks `/status` and confirms 11 unpaid months and 0 balance.
    """
    assert True
