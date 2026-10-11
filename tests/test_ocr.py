"""Comprehensive unit tests for Multimodal Gemini Flash OCR (src/wri_kas_bot/ocr.py)."""

from unittest.mock import AsyncMock, patch

import pytest
import respx

from wri_kas_bot.ocr import (
    GEMINI_API_URL,
    OCRReceiptResult,
    _call_gemini_api,
    _detect_mime_type,
    _parse_amount,
    extract_receipt_data,
)


def test_detect_mime_type_magic_bytes():
    """Verify MIME type sniffing based on image header bytes."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert _detect_mime_type(png_bytes) == "image/png"

    webp_bytes = b"RIFF\x1e\x00\x00\x00WEBPVP8 "
    assert _detect_mime_type(webp_bytes) == "image/webp"

    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    assert _detect_mime_type(jpeg_bytes) == "image/jpeg"

    unknown_bytes = b"random_binary_stream"
    assert _detect_mime_type(unknown_bytes) == "image/jpeg"


def test_parse_amount_currency_sanitization_matrix():
    """Verify robust parsing of Indonesian currency formats, cents, and edge cases."""
    # Direct numbers
    assert _parse_amount(20000) == 20000
    assert _parse_amount(20000.0) == 20000
    assert _parse_amount(0) is None
    assert _parse_amount(-5000) is None

    # Standard strings
    assert _parse_amount("20000") == 20000
    assert _parse_amount("Rp 20.000") == 20000
    assert _parse_amount("Rp. 50.000") == 50000

    # Decimal cents stripping (,00 and .00)
    assert _parse_amount("20.000,00") == 20000
    assert _parse_amount("Rp 20.000,00") == 20000
    assert _parse_amount("20,000.00") == 20000
    assert _parse_amount("50.000,50") == 50000

    # Large values (millions)
    assert _parse_amount(1000000) == 1000000
    assert _parse_amount("1.000.000") == 1000000
    assert _parse_amount("Rp 1.000.000,00") == 1000000

    # Null, empty, and non-numeric representations
    assert _parse_amount(None) is None
    assert _parse_amount("") is None
    assert _parse_amount("none") is None
    assert _parse_amount("null") is None
    assert _parse_amount("tidak terbaca") is None


@pytest.mark.asyncio
async def test_call_gemini_api_empty_api_key():
    """Verify empty api_key returns empty dictionary without invoking network."""
    res = await _call_gemini_api(b"fake-bytes", api_key="")
    assert res == {}


@pytest.mark.asyncio
async def test_call_gemini_api_http_success(respx_mock: respx.MockRouter):
    """Verify _call_gemini_api executes correct HTTP request and parses response."""
    endpoint_pattern = GEMINI_API_URL.split("?")[0]
    gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"nominal": 20000, "bank": "Bank Jago", '
                                '"date": "2026-10-11", "sender_name": "Alice"}'
                            )
                        }
                    ]
                }
            }
        ]
    }
    route = respx_mock.post(endpoint_pattern).respond(status_code=200, json=gemini_response)

    data = await _call_gemini_api(b"image_bytes_here", api_key="secret-gemini-key")
    assert route.called
    assert data["nominal"] == 20000
    assert data["bank"] == "Bank Jago"
    assert data["sender_name"] == "Alice"


@pytest.mark.asyncio
async def test_call_gemini_api_markdown_fence_unwrapping(respx_mock: respx.MockRouter):
    """Verify markdown fences (```json ... ```) are cleanly stripped before JSON parsing."""
    endpoint_pattern = GEMINI_API_URL.split("?")[0]
    gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "```json\n"
                                '{"nominal": 30000, "bank": "BCA", "sender_name": "Bob"}\n'
                                "```"
                            )
                        }
                    ]
                }
            }
        ]
    }
    respx_mock.post(endpoint_pattern).respond(status_code=200, json=gemini_response)

    data = await _call_gemini_api(b"image_bytes_here", api_key="test-key")
    assert data["nominal"] == 30000
    assert data["bank"] == "BCA"


@pytest.mark.asyncio
async def test_call_gemini_api_malformed_json_fallback(respx_mock: respx.MockRouter):
    """Verify unparseable response text gracefully falls back to empty dict."""
    endpoint_pattern = GEMINI_API_URL.split("?")[0]
    gemini_response = {
        "candidates": [{"content": {"parts": [{"text": "Sorry, I cannot read this receipt."}]}}]
    }
    respx_mock.post(endpoint_pattern).respond(status_code=200, json=gemini_response)

    data = await _call_gemini_api(b"image_bytes_here", api_key="test-key")
    assert data == {}


@pytest.mark.asyncio
async def test_extract_receipt_data_field_normalization():
    """Verify extract_receipt_data normalizes all fields into OCRReceiptResult."""
    fake_json = {
        "nominal": "Rp 20.000,00",
        "bank": "BRI",
        "date": "2026-10-11",
        "sender_name": "Alice",
        "audit_meta": "test-123",
    }
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = fake_json
        result: OCRReceiptResult = await extract_receipt_data(b"fake-bytes", api_key="key")

    assert result.amount == 20000
    assert result.bank == "BRI"
    assert result.date == "2026-10-11"
    assert result.sender_name == "Alice"
    assert result.raw_response == fake_json


@pytest.mark.asyncio
async def test_extract_receipt_data_alias_fallback():
    """Verify fallback when model returns 'amount' or 'sender' instead of standard keys."""
    fake_json = {
        "amount": 50000,
        "bank": "Mandiri",
        "sender": "Bob",
    }
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = fake_json
        result = await extract_receipt_data(b"fake-bytes", api_key="key")

    assert result.amount == 50000
    assert result.bank == "Mandiri"
    assert result.sender_name == "Bob"
    assert result.date is None


@pytest.mark.asyncio
async def test_extract_receipt_data_exception_resilience():
    """Verify network exceptions or HTTP 503 safely yield default null OCRReceiptResult."""
    with patch("wri_kas_bot.ocr._call_gemini_api", new_callable=AsyncMock) as mock_api:
        mock_api.side_effect = RuntimeError("API connection timed out")
        result = await extract_receipt_data(b"fake-bytes", api_key="key")

    assert result.amount is None
    assert result.bank is None
    assert result.date is None
    assert result.sender_name is None
    assert result.raw_response == {}
