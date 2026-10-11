"""Tier 3: Cross-Feature Interactions: Approval to Outbox & Google Sheets Sync."""

from unittest.mock import MagicMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import TransactionRepository


@pytest.mark.asyncio
async def test_flow_approval_outbox_drive_and_sheets_sync(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path
):
    """Pairwise: Approval creates task -> Worker uploads to Drive -> Updates Sheets -> SYNCED."""
    test_img = tmp_path / "test_receipt.jpg"
    test_img.write_bytes(b"JPEG_MOCK_DATA")

    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(
            member_id=1,
            amount_paid=20000,
            payment_method="TRANSFER_JAGO",
            local_image_path=str(test_img),
            ocr_amount=20000,
        )
        tx_repo.approve(
            tx_id=tx_id,
            balance_before=0,
            balance_after=0,
            months_filled=["Februari", "Maret"],
            approved_by="6281100000099@s.whatsapp.net",
        )

    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.sync_status == "SYNCED"
        assert tx.proof_drive_url is not None
        assert "drive.google.com" in tx.proof_drive_url

    assert len(mock_sheets_client.updated_cells) == 2
    assert len(mock_sheets_client.appended_rows) == 1


@pytest.mark.asyncio
async def test_flow_outbox_retry_backoff_on_sheets_timeout(
    initialized_db, mock_drive_client, mock_sheets_client
):
    """Pairwise: Transient error during Google Sheets sync increments retry count."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=10000)
        tx_repo.approve(
            tx_id=tx_id,
            balance_before=0,
            balance_after=0,
            months_filled=["Februari"],
            approved_by="treasurer",
        )

    failing_sheets = MagicMock()
    failing_sheets.update_payment_month.side_effect = RuntimeError("Google Sheets API Timeout 503")

    worker = OutboxWorker(db=db, sheets_client=failing_sheets, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed == 0

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.sync_status == "PENDING"
        assert tx.retry_count == 1
        assert "Google Sheets API Timeout 503" in (tx.sync_error or "")
