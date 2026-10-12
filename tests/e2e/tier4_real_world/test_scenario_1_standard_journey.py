"""Tier 4: Real-World Scenarios: Scenario 1: Standard Transfer Payment Journey (Alice)."""

import base64
from unittest.mock import AsyncMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.handlers.receipt_handler import handle_receipt
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.ocr import OCRResult, ReceiptOCR
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


@pytest.mark.asyncio
async def test_scenario_1_alice_pays_three_months(
    initialized_db,
    mock_evolution_api,
    mock_drive_client,
    mock_sheets_client,
    make_webhook_payload,
    tmp_path,
    monkeypatch,
):
    """Scenario 1: Full lifecycle of standard 3-month payment."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    b64_img = base64.b64encode(b"ALICE_30K_RECEIPT_BYTES").decode("utf-8")
    receipt_dict = make_webhook_payload(
        sender_phone="6281100000001",
        image_base64=b64_img,
        message_text="Bukti kas Feb-Apr",
    )
    receipt_payload = WebhookPayload.model_validate(receipt_dict)

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=30000, bank="BCA", raw_text="Transfer BCA Rp 30.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_MSG_1"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "NOTIF_STANZA_1"}})

    # 1. Ingest receipt
    ingest_res = await handle_receipt(receipt_payload, db=db, evolution=evo, ocr_engine=mock_ocr)
    assert ingest_res["status"] == "ok"
    tx_id = ingest_res["transaction_id"]

    # 2. Treasurer swipe-replies /acc
    acc_dict = make_webhook_payload(
        sender_phone="6281100000099",
        message_text="/acc",
        quoted_id="NOTIF_STANZA_1",
        quoted_text="Receipt card",
    )
    acc_payload = WebhookPayload.model_validate(acc_dict)
    acc_res = await handle_treasurer(acc_payload, db=db, evolution=evo)
    assert acc_res["status"] == "approved"

    # 3. Check FIFO allocation in SQLite
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 3
        assert [d.month for d in paid_dues] == ["Februari", "Maret", "April"]

    # 4. Outbox worker syncs to Drive & Sheets
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1
    assert len(mock_sheets_client.updated_cells) == 3

    # 5. Alice queries /status
    status_dict = make_webhook_payload(sender_phone="6281100000001", message_text="/status")
    status_payload = WebhookPayload.model_validate(status_dict)
    status_res = await handle_member(status_payload, db=db, evolution=evo)
    assert status_res["status"] == "ok"
    assert status_res["unpaid_count"] == 8
    assert status_res["overdue_count"] == 6
    assert status_res["future_count"] == 2
    assert status_res["net_bill"] == 60000
