"""Multimodal Gemini Flash receipt OCR module for wri-kas-bot.

Extracts transaction metadata (amount, date, bank, sender) from receipt images
using Google Gemini Flash Vision via asynchronous HTTP requests with structured JSON.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
)

PROMPT_RECEIPT_EXTRACTION = (
    "Analyze this Indonesian transfer receipt or payment proof. "
    "Extract the following financial details into a strict JSON object:\n\n"
    '1. "nominal": Total amount transferred in Indonesian Rupiah (integer or number, e.g. 20000). '
    'Look for keywords: "Jumlah", "Nominal", "Total Transfer", "Total Transaksi", "Total Bayar". '
    "Exclude admin fees if listed separately. "
    "If unreadable, blurry, or not a payment proof, set to null.\n"
    '2. "bank": Name of the bank or e-wallet involved (e.g. "Bank Jago", "BCA", "Mandiri", '
    '"BRI", "BNI", "SeaBank", "DANA", "GoPay", "OVO", "QRIS"). If not visible, set to null.\n'
    '3. "date": Transaction date formatted as YYYY-MM-DD if recognizable, or date string as shown. '
    "If not visible, set to null.\n"
    '4. "sender_name": Name of the sender/payer/account holder if displayed (look for "Pengirim", '
    '"Dari", "Nama Pengirim"). If not visible, set to null.\n'
    '5. "confidence": Confidence score between 0.0 and 1.0 based on image readability.\n\n'
    "Return ONLY a valid JSON object matching these keys."
)


@dataclass
class OCRReceiptResult:
    """Structured extraction result from receipt OCR analysis."""

    amount: int | None
    date: str | None
    sender_name: str | None
    bank: str | None
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def nominal(self) -> int | None:
        """Alias for amount to support diverse consumer naming."""
        return self.amount


def _detect_mime_type(image_bytes: bytes) -> str:
    """Sniff MIME type from image magic bytes, defaulting to image/jpeg."""
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _clean_str(val: Any) -> str | None:
    """Clean string values, mapping null/empty representations to None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    return s


def _parse_amount(raw_val: Any) -> int | None:
    """Parse and normalize Indonesian currency values into integer IDR."""
    if raw_val is None or isinstance(raw_val, bool):
        return None

    if isinstance(raw_val, (int, float)):
        val_int = int(raw_val)
        return val_int if val_int > 0 else None

    if not isinstance(raw_val, str):
        return None

    cleaned = raw_val.strip()
    if not cleaned or cleaned.lower() in ("none", "null"):
        return None

    # Strip decimal cents: ,00 (Indonesian) or .00 (English 2-digit cents)
    cleaned = re.sub(r",\d{2}$", "", cleaned)
    cleaned = re.sub(r"\.\d{2}$", "", cleaned)

    # Strip non-digits
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return None

    result = int(digits)
    return result if result > 0 else None


async def _call_gemini_api(image_bytes: bytes, api_key: str) -> dict[str, Any]:
    """Execute raw HTTP POST to Google Gemini Flash API and return parsed JSON dictionary."""
    if not api_key:
        logger.warning("gemini_api_key is empty, returning empty dictionary")
        return {}

    b64_image = base64.b64encode(image_bytes).decode("ascii")
    mime_type = _detect_mime_type(image_bytes)

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT_RECEIPT_EXTRACTION},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_image,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(
            GEMINI_API_URL,
            params={"key": api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        logger.warning("Gemini response contained no candidates")
        return {}

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        logger.warning("Gemini candidate content contained no parts")
        return {}

    raw_text = parts[0].get("text", "").strip()
    if not raw_text:
        return {}

    # Strip markdown backticks if wrapped
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as err:
        logger.error("Failed to decode JSON from Gemini response: %s", err)
        return {}


async def extract_receipt_data(image_bytes: bytes, api_key: str) -> OCRReceiptResult:
    """Extract receipt metadata using Gemini Flash Vision.

    Gracefully falls back to null values on network or extraction errors.
    """
    try:
        raw_dict = await _call_gemini_api(image_bytes, api_key)
    except Exception as exc:
        logger.error("Gemini API call failed with exception: %s", exc)
        return OCRReceiptResult(
            amount=None,
            date=None,
            sender_name=None,
            bank=None,
            raw_response={},
        )

    if not isinstance(raw_dict, dict):
        raw_dict = {}

    raw_amount = raw_dict.get("nominal") if "nominal" in raw_dict else raw_dict.get("amount")
    parsed_amount = _parse_amount(raw_amount)

    date_val = _clean_str(raw_dict.get("date"))
    sender_val = _clean_str(raw_dict.get("sender_name") or raw_dict.get("sender"))
    bank_val = _clean_str(raw_dict.get("bank"))

    return OCRReceiptResult(
        amount=parsed_amount,
        date=date_val,
        sender_name=sender_val,
        bank=bank_val,
        raw_response=raw_dict,
    )


class ReceiptOCR:
    """Object-oriented wrapper around Gemini receipt extraction."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or ""

    async def extract_receipt(self, image_bytes: bytes) -> OCRReceiptResult:
        """Extract receipt transaction details from image bytes."""
        return await extract_receipt_data(image_bytes, self.api_key)
