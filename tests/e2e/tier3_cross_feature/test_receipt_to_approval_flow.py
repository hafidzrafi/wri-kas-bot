"""Tier 3: Cross-Feature Interactions: Receipt Ingestion to Treasurer Approval Flow."""

import base64
from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.receipt_handler import handle_receipt
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.ocr import OCRResult, ReceiptOCR
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def _make_receipt_payload(
    phone: str = "6281100000001", msg_id: str = "MSG_MEMBER_1"
) -> WebhookPayload:
    b64_img = base64.b64encode(b"RECEIPT_JPG").decode("utf-8")
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {"remoteJid": f"{phone}@s.whatsapp.net", "fromMe": False, "id": msg_id},
                "message": {"imageMessage": {"mimetype": "image/jpeg"}, "base64": b64_img},
                "messageTimestamp": 1700000000,
            },
        }
    )


def _make_treasurer_payload(text: str, quoted_id: str) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": "6281100000099@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_CMD",
                },
                "message": {
                    "extendedTextMessage": {
                        "text": text,
                        "contextInfo": {
                            "stanzaId": quoted_id,
                            "quotedMessage": {"conversation": "Receipt"},
                        },
                    }
                },
                "messageTimestamp": 1700000000,
            },
        }
    )


@pytest.mark.asyncio
async def test_flow_receipt_ingest_ocr_and_acc_auto_nominal(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Pairwise: Member uploads receipt -> OCR detects 20k -> Treasurer /acc -> 2 months clear."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=20000, bank="Bank Jago", raw_text="Rp 20.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_ID"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "NOTIF_STANZA_1"}})

    # 1. Receipt Ingestion
    ingest_res = await handle_receipt(
        _make_receipt_payload(), db=db, evolution=evo, ocr_engine=mock_ocr
    )
    tx_id = ingest_res["transaction_id"]

    # 2. Treasurer Approval via Swipe-Reply
    acc_payload = _make_treasurer_payload("/acc", quoted_id="NOTIF_STANZA_1")
    acc_res = await handle_treasurer(acc_payload, db=db, evolution=evo)
    assert acc_res["status"] == "approved"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 2
        assert [d.month for d in paid_dues] == ["Februari", "Maret"]


@pytest.mark.asyncio
async def test_flow_receipt_ocr_blurry_treasurer_explicit_acc(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Pairwise: OCR null nominal -> Treasurer swipe-replies /acc 30000 -> 3 months cleared."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(nominal=None, bank=None, raw_text="")

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_ID"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "NOTIF_STANZA_2"}})

    await handle_receipt(_make_receipt_payload(), db=db, evolution=evo, ocr_engine=mock_ocr)

    acc_payload = _make_treasurer_payload("/acc 30000", quoted_id="NOTIF_STANZA_2")
    acc_res = await handle_treasurer(acc_payload, db=db, evolution=evo)
    assert acc_res["status"] == "approved"

    with db.connect() as conn:
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 3


@pytest.mark.asyncio
async def test_flow_receipt_treasurer_rejection(
    initialized_db, tmp_path, monkeypatch, mock_evolution_api
):
    """Pairwise: Member uploads receipt -> Treasurer /reject -> Transaction marked REJECTED."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_ID"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "NOTIF_STANZA_3"}})

    ingest_res = await handle_receipt(
        _make_receipt_payload(), db=db, evolution=evo, ocr_engine=mock_ocr
    )
    tx_id = ingest_res["transaction_id"]

    rej_payload = _make_treasurer_payload("/reject Bukti palsu", quoted_id="NOTIF_STANZA_3")
    rej_res = await handle_treasurer(rej_payload, db=db, evolution=evo)
    assert rej_res["status"] == "rejected"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REJECTED"
        assert tx.rejection_reason == "Bukti palsu"
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 0
