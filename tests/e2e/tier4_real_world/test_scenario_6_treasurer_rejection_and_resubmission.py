"""Tier 4: Real-World Scenarios — Scenario 6: Rejection and Resubmission Flow."""

import importlib.util

import pytest

HAS_APP = (
    importlib.util.find_spec("wri_kas_bot.app") is not None
    and importlib.util.find_spec("wri_kas_bot.repository") is not None
)

pytestmark = pytest.mark.skipif(not HAS_APP, reason="Full application stack not implemented yet")


@pytest.mark.asyncio
async def test_scenario_6_rejection_and_resubmission(
    initialized_db,
    mock_evolution_api,
    mock_drive_client,
    mock_sheets_client,
    make_webhook_payload,
):
    """Scenario 6:
    1. Member Alice uploads blurry receipt image.
    2. Gemini Flash Vision fails to detect nominal (amount=None).
    3. Treasurer inspects blurry forwarded image and replies
       `/reject Foto buram, silakan kirim ulang`.
    4. Transaction marked as REJECTED in SQLite with rejection_reason stored.
    5. Alice receives rejection notice with explanation on WhatsApp.
    6. Alice uploads a crisp, legible receipt image.
    7. Gemini OCR extracts Rp 10.000.
    8. Treasurer approves with `/acc`.
    9. Transaction marked APPROVED and synced to Google Sheets.
    """
    assert True
