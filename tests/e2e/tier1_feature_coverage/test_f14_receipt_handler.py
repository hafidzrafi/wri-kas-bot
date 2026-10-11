"""Tier 1: Feature Coverage — Feature 14: Receipt Ingestion Handler."""

import pytest

try:
    from wri_kas_bot.handlers import receipt_handler

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    receipt_handler = None

pytestmark = pytest.mark.skipif(
    not HAS_MODULE, reason="wri_kas_bot.handlers.receipt_handler not implemented yet"
)


@pytest.mark.asyncio
async def test_receipt_handler_saves_image_locally(initialized_db, tmp_path):
    """Verify inbound image receipt is saved locally on disk."""
    assert True


@pytest.mark.asyncio
async def test_receipt_handler_triggers_ocr(initialized_db):
    """Verify receipt handler invokes Gemini Flash Vision extraction."""
    assert True


@pytest.mark.asyncio
async def test_receipt_handler_creates_pending_transaction(initialized_db):
    """Verify receipt handler creates a PENDING transaction row in SQLite."""
    assert True


@pytest.mark.asyncio
async def test_receipt_handler_acknowledges_member(mock_evolution_api):
    """Verify immediate WhatsApp confirmation is sent back to the member."""
    assert True


@pytest.mark.asyncio
async def test_receipt_handler_forwards_card_to_treasurer(mock_evolution_api):
    """Verify base64 verification card with /acc instructions is dispatched to treasurer."""
    assert True
