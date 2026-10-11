"""Tier 1: Feature Coverage: Feature 8: Multimodal Gemini OCR (ocr.py)."""

from unittest.mock import AsyncMock, patch

import pytest

from wri_kas_bot.ocr import extract_receipt_data


@pytest.mark.asyncio
async def test_ocr_extract_receipt_valid_json():
    """Verify successful OCR extraction parses nominal, date, bank, and sender."""
    fake_img = b"fake-receipt-bytes"
    fake_json = {
        "nominal": 20000,
        "bank": "Bank Jago",
        "date": "2026-10-11",
        "sender_name": "Alice",
        "confidence": 0.98,
    }

    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_json
        result = await extract_receipt_data(fake_img, api_key="mock-key")

    assert result.amount == 20000
    assert result.bank == "Bank Jago"
    assert result.date == "2026-10-11"
    assert result.sender_name == "Alice"


@pytest.mark.asyncio
async def test_ocr_handles_indonesian_rupiah_format():
    """Verify string formatting like 'Rp 20.000' is normalized to integer 20000."""
    fake_img = b"fake-receipt-bytes"
    fake_json = {"nominal": "Rp 20.000", "bank": "BCA", "date": "2026-10-11"}

    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_json
        result = await extract_receipt_data(fake_img, api_key="mock-key")

    assert result.amount == 20000


@pytest.mark.asyncio
async def test_ocr_handles_blurry_unreadable_image():
    """Verify blurry receipt gracefully yields None for amount."""
    fake_img = b"blurry-bytes"
    fake_json = {"nominal": None, "bank": None, "confidence": 0.0}

    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_json
        result = await extract_receipt_data(fake_img, api_key="mock-key")

    assert result.amount is None


@pytest.mark.asyncio
async def test_ocr_detects_common_indonesian_banks():
    """Verify common Indonesian bank names are recognized."""
    fake_img = b"mandiri-receipt"
    fake_json = {"nominal": 10000, "bank": "Mandiri", "date": "2026-10-11"}

    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_json
        result = await extract_receipt_data(fake_img, api_key="mock-key")

    assert result.bank == "Mandiri"


@pytest.mark.asyncio
async def test_ocr_raw_response_preserved():
    """Verify the original API dictionary is retained in OCRReceiptResult."""
    fake_img = b"receipt"
    fake_json = {"nominal": 10000, "bank": "SeaBank", "custom_tag": "audit-123"}

    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_json
        result = await extract_receipt_data(fake_img, api_key="mock-key")

    assert isinstance(result.raw_response, dict)
    assert result.raw_response.get("custom_tag") == "audit-123"
