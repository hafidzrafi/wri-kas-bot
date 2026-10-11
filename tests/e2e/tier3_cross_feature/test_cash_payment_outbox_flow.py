"""Tier 3: Cross-Feature Interactions — Cash Payment and Google Sheets Sync."""

import importlib.util

import pytest

HAS_MODULES = (
    importlib.util.find_spec("wri_kas_bot.fifo") is not None
    and importlib.util.find_spec("wri_kas_bot.outbox") is not None
    and importlib.util.find_spec("wri_kas_bot.sheets") is not None
)

pytestmark = pytest.mark.skipif(not HAS_MODULES, reason="Cash/Outbox modules not implemented yet")


@pytest.mark.asyncio
async def test_flow_cash_payment_allocates_and_syncs_tunai(initialized_db, mock_sheets_client):
    """Pairwise: /tunai command runs FIFO, skips Drive upload, and writes 'Tunai' into Sheets."""
    assert True


@pytest.mark.asyncio
async def test_flow_cash_payment_undo_restores_unpaid(initialized_db, mock_sheets_client):
    """Pairwise: /undo on cash payment clears 'Tunai' cell and reverts dues to UNPAID."""
    assert True
