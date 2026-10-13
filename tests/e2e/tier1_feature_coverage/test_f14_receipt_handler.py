"""Tier 1: Feature Coverage: Feature 14: Receipt Ingestion Handler."""

import base64
from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.receipt_handler import handle_receipt
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.ocr import OCRResult, ReceiptOCR


def _make_receipt_payload(
    sender_phone: str = "6281100000001",
    image_bytes: bytes = b"FAKE_JPEG_IMAGE_CONTENT",
    msg_id: str = "MSG_RECEIPT_1",
) -> WebhookPayload:
    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": f"{sender_phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": msg_id,
                },
                "message": {
                    "imageMessage": {
                        "mimetype": "image/jpeg",
                        "caption": "Bukti transfer kas",
                    },
                    "base64": b64_img,
                },
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_receipt_handler_saves_image_locally(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Verify inbound image receipt is saved locally on disk."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    payload = _make_receipt_payload(image_bytes=b"IMG_BYTES_123")

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Transfer Rp 10.000"
    )

    res = await handle_receipt(payload, db=db, ocr_engine=mock_ocr)
    assert res["status"] == "ok"

    saved_files = list((tmp_path / "receipts").glob("*"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == b"IMG_BYTES_123"


@pytest.mark.asyncio
async def test_receipt_handler_triggers_ocr(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Verify receipt handler invokes Gemini Flash Vision extraction."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    payload = _make_receipt_payload()

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=20000, bank="BCA", raw_text="Transfer Rp 20.000"
    )

    res = await handle_receipt(payload, db=db, ocr_engine=mock_ocr)
    mock_ocr.extract_receipt.assert_awaited_once()

    with db.connect() as conn:
        tx = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (res["transaction_id"],)
        ).fetchone()
        assert tx["ocr_amount"] == 20000
        assert tx["ocr_bank"] == "BCA"


@pytest.mark.asyncio
async def test_receipt_handler_creates_pending_transaction(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Verify receipt handler creates a PENDING transaction row in SQLite."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    payload = _make_receipt_payload(msg_id="MSG_PENDING_TX")

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    res = await handle_receipt(payload, db=db, ocr_engine=mock_ocr)
    tx_id = res["transaction_id"]

    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        assert tx is not None
        assert tx["status"] == "PENDING"
        assert tx["amount_paid"] == 10000
        assert tx["wa_message_id"] == "MSG_PENDING_TX"


@pytest.mark.asyncio
async def test_receipt_handler_acknowledges_member(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Verify immediate WhatsApp confirmation is sent back to the member."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    payload = _make_receipt_payload()

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_MSG_ID"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "TREASURER_NOTIF_ID"}})

    await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)

    evo.send_text.assert_awaited_once()
    call_args = evo.send_text.call_args
    assert "6281100000001" in call_args.kwargs["recipient"]
    assert "diterima" in call_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_receipt_handler_forwards_card_to_treasurer(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Verify base64 verification card with /acc instructions is dispatched to treasurer."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    payload = _make_receipt_payload()

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_MSG_ID"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "TREASURER_NOTIF_ID"}})

    await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)

    evo.send_media.assert_awaited_once()
    call_kwargs = evo.send_media.call_args.kwargs
    assert "/acc" in call_kwargs["caption"]
    assert "6281100000099" in call_kwargs["recipient"]


@pytest.mark.asyncio
async def test_receipt_handler_overdue_months_filtered_to_calendar_month(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Verify treasurer card calculates overdue months up to current calendar month."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    # Mark months 2 through 9 (Feb-Sep) as PAID for member 1
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE monthly_dues SET status = 'PAID'
            WHERE member_id = 1 AND year = 2026 AND month_index BETWEEN 2 AND 9
            """
        )

    payload = _make_receipt_payload()
    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_MSG_ID"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "TREASURER_NOTIF_ID"}})

    await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)

    call_kwargs = evo.send_media.call_args.kwargs
    caption = call_kwargs["caption"]
    # In October 2026, only October is overdue (1 bln, 10k), Nov & Dec are not overdue!
    assert "Tunggakan: Oktober (1 bln)" in caption
    assert "Tagihan: Rp 10,000" in caption
