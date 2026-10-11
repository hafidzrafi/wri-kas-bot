"""Tier 4: Real-World Scenarios: Scenario 6: Rejection and Resubmission Flow."""

import base64
from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.receipt_handler import handle_receipt
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.ocr import OCRResult, ReceiptOCR
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def _make_receipt_payload(b64_data: str, msg_id: str) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {"remoteJid": "6281100000001@s.whatsapp.net", "fromMe": False, "id": msg_id},
                "message": {"imageMessage": {"mimetype": "image/jpeg"}, "base64": b64_data},
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
async def test_scenario_6_rejection_and_resubmission(
    initialized_db,
    mock_evolution_api,
    mock_drive_client,
    mock_sheets_client,
    tmp_path,
    monkeypatch,
):
    """Scenario 6: Rejection followed by successful resubmission."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_MSG"}})
    evo.send_media = AsyncMock(
        side_effect=[
            {"key": {"id": "STANZA_BLURRY"}},
            {"key": {"id": "STANZA_CLEAN"}},
        ]
    )

    # 1. Blurry image submitted
    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(nominal=None, bank=None, raw_text="")
    res1 = await handle_receipt(
        _make_receipt_payload(base64.b64encode(b"BLURRY").decode("utf-8"), "MSG_BLURRY"),
        db=db,
        evolution=evo,
        ocr_engine=mock_ocr,
    )
    tx1_id = res1["transaction_id"]

    # 2. Treasurer rejects blurry receipt
    rej_res = await handle_treasurer(
        _make_treasurer_payload("/reject Foto buram, silakan kirim ulang", "STANZA_BLURRY"),
        db=db,
        evolution=evo,
    )
    assert rej_res["status"] == "rejected"

    with db.connect() as conn:
        tx1 = TransactionRepository(conn).get_by_id(tx1_id)
        assert tx1.status == "REJECTED"
        assert tx1.rejection_reason == "Foto buram, silakan kirim ulang"

    # 3. Clean image resubmitted
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )
    res2 = await handle_receipt(
        _make_receipt_payload(base64.b64encode(b"CLEAN").decode("utf-8"), "MSG_CLEAN"),
        db=db,
        evolution=evo,
        ocr_engine=mock_ocr,
    )
    tx2_id = res2["transaction_id"]

    # 4. Treasurer approves
    acc_res = await handle_treasurer(
        _make_treasurer_payload("/acc", "STANZA_CLEAN"),
        db=db,
        evolution=evo,
    )
    assert acc_res["status"] == "approved"

    with db.connect() as conn:
        tx2 = TransactionRepository(conn).get_by_id(tx2_id)
        assert tx2.status == "APPROVED"
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 1
        assert paid_dues[0].month == "Februari"

    # 5. Outbox worker syncs to sheets
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1
    assert len(mock_sheets_client.updated_cells) == 1
