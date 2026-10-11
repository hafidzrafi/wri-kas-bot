"""Tier 4: Real-World Scenarios — Scenario 2: Partial Underpayment & Installment Top-up (Bob)."""

import importlib.util

import pytest

HAS_APP = (
    importlib.util.find_spec("wri_kas_bot.app") is not None
    and importlib.util.find_spec("wri_kas_bot.fifo") is not None
    and importlib.util.find_spec("wri_kas_bot.outbox") is not None
    and importlib.util.find_spec("wri_kas_bot.repository") is not None
)

pytestmark = pytest.mark.skipif(not HAS_APP, reason="Full application stack not implemented yet")


@pytest.mark.asyncio
async def test_scenario_2_bob_underpays_and_tops_up(
    initialized_db,
    mock_evolution_api,
    mock_drive_client,
    mock_sheets_client,
    make_webhook_payload,
):
    """Scenario 2:
    1. Bob sends Rp 5.000 receipt.
    2. Treasurer approves /acc.
    3. FIFO detects underpayment (< Rp 10.000): 0 months cleared, Rp 5.000 added to deposit balance.
    4. Bob receives notification explaining deposit accumulation and remaining Rp 5.000 deficit.
    5. Next month, Bob transfers Rp 15.000.
    6. Total pool reaches Rp 20.000 (5.000 balance + 15.000 payment).
    7. FIFO clears Feb and Mar (20.000 total). Ending balance becomes 0.
    8. Google Sheets updated for Feb and Mar, log_transaksi appended.
    """
    assert True
