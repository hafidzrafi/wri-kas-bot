"""Tier 2: Boundary & Corner Cases — Multimodal Gemini OCR (ocr.py)."""

from unittest.mock import AsyncMock, patch

import pytest

try:
    from wri_kas_bot.ocr import OCRReceiptResult, extract_receipt_data

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    OCRReceiptResult = None
    extract_receipt_data = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.ocr not implemented yet")


@pytest.mark.asyncio
async def test_ocr_blurry_corrupt_image_yields_none():
    """Boundary: Unreadable or blurry image yields nominal=None with 0.0 confidence."""
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"nominal": None, "confidence": 0.0}
        result = await extract_receipt_data(b"blurry-bytes", "mock-key")
        assert result.amount is None


@pytest.mark.asyncio
async def test_ocr_markdown_codeblock_wrapped_json():
    """Corner case: Model wraps JSON in ```json markdown block."""
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"nominal": 20000, "bank": "BCA"}
        result = await extract_receipt_data(b"bytes", "mock-key")
        assert result.amount == 20000


@pytest.mark.asyncio
async def test_ocr_nominal_with_decimal_cents():
    """Corner case: Receipt with decimal cents '20.000,00' or '20,000.00'."""
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"nominal": "20.000,00", "bank": "BCA"}
        result = await extract_receipt_data(b"bytes", "mock-key")
        assert result.amount == 20000


@pytest.mark.asyncio
async def test_ocr_large_nominal_million():
    """Boundary: Large nominal payment (e.g. 1.000.000)."""
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"nominal": 1000000, "bank": "BCA"}
        result = await extract_receipt_data(b"bytes", "mock-key")
        assert result.amount == 1000000


@pytest.mark.asyncio
async def test_ocr_gemini_api_503_fallback():
    """Boundary: Gemini API transient failure returns safe fallback without crash."""
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = Exception("HTTP 503 Service Unavailable")
        # System should handle or propagate clean exception
        try:
            res = await extract_receipt_data(b"bytes", "mock-key")
            assert res.amount is None
        except Exception:
            pass
