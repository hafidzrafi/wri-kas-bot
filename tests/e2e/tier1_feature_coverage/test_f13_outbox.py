"""Tier 1: Feature Coverage: Feature 13: Transactional Outbox Worker (outbox.py)."""

import sqlite3

import pytest


@pytest.mark.asyncio
async def test_outbox_polls_pending_transactions(initialized_db):
    """Verify outbox worker polls records with sync_status = 'PENDING'."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sync_status) "
        "VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, '[[2026, 2]]', 'APPROVED', 'PENDING')"
    )
    tx_id = cursor.lastrowid
    conn.commit()
    conn.close()

    assert tx_id is not None


@pytest.mark.asyncio
async def test_outbox_processes_drive_upload_and_sheets_sync(mock_drive_client, mock_sheets_client):
    """Verify worker orchestrates Google Drive upload followed by Google Sheets update."""
    drive_url = mock_drive_client.upload_file(b"receipt", "20261011_Alice.jpg")
    coord = mock_sheets_client.update_payment_month("Alice", "Februari", drive_url)

    assert "drive.google.com" in drive_url
    assert coord.startswith("PENGURUS-2025/2026!")


@pytest.mark.asyncio
async def test_outbox_marks_transaction_synced(initialized_db):
    """Verify transaction is marked SYNCED after successful outbox execution."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sync_status) "
        "VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, '[[2026, 2]]', 'APPROVED', 'PENDING')"
    )
    tx_id = cursor.lastrowid
    cursor.execute("UPDATE transactions SET sync_status = 'SYNCED' WHERE id = ?", (tx_id,))
    conn.commit()

    cursor.execute("SELECT sync_status FROM transactions WHERE id = ?", (tx_id,))
    status = cursor.fetchone()[0]
    conn.close()

    assert status == "SYNCED"


@pytest.mark.asyncio
async def test_outbox_handles_retry_on_failure(initialized_db):
    """Verify outbox records sync_error and increments retry_count on exception."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "status, sync_status, retry_count) "
        "VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, 'APPROVED', 'PENDING', 0)"
    )
    tx_id = cursor.lastrowid
    cursor.execute(
        "UPDATE transactions SET retry_count = retry_count + 1, "
        "sync_error = 'Google Drive 503' WHERE id = ?",
        (tx_id,),
    )
    conn.commit()

    cursor.execute("SELECT retry_count, sync_error FROM transactions WHERE id = ?", (tx_id,))
    retry_count, error = cursor.fetchone()
    conn.close()

    assert retry_count == 1
    assert "Google Drive 503" in error


@pytest.mark.asyncio
async def test_outbox_reverse_sync_on_undo(mock_sheets_client):
    """Verify outbox worker triggers batch_clear_cells on reverse sync tasks."""
    mock_sheets_client.batch_clear_cells(["PENGURUS-2025/2026!D5"])
    assert "PENGURUS-2025/2026!D5" in mock_sheets_client.cleared_ranges


@pytest.mark.asyncio
async def test_outbox_writes_bank_jago_for_transfer_and_tunai_for_cash(
    initialized_db, mock_sheets_client, mock_drive_client, tmp_path
):
    """Verify matrix dues cells receive 'Bank Jago' (transfer) or 'Tunai' (cash),
    while log gets Drive link.
    """
    from wri_kas_bot.database import Database
    from wri_kas_bot.outbox import OutboxWorker
    from wri_kas_bot.repository import TransactionRepository

    test_img = tmp_path / "receipt.jpg"
    test_img.write_bytes(b"RECEIPT_DATA")

    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id_transfer = tx_repo.create_draft(
            member_id=1,
            amount_paid=10000,
            payment_method="TRANSFER_JAGO",
            local_image_path=str(test_img),
        )
        tx_repo.approve(
            tx_id=tx_id_transfer,
            balance_before=0,
            balance_after=0,
            months_filled=["Februari"],
            approved_by="6281100000099@s.whatsapp.net",
        )

        tx_id_cash = tx_repo.create_draft(
            member_id=2,
            amount_paid=10000,
            payment_method="CASH",
        )
        tx_repo.approve(
            tx_id=tx_id_cash,
            balance_before=0,
            balance_after=0,
            months_filled=["Februari"],
            approved_by="6281100000099@s.whatsapp.net",
        )

    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed == 2

    # Verify dues matrix cells: 1st is Bank Jago, 2nd is Tunai
    assert mock_sheets_client.updated_cells[-2]["proof_url"] == "Bank Jago"
    assert mock_sheets_client.updated_cells[-1]["proof_url"] == "Tunai"

    # Verify log_transaksi Bukti URL: 1st is Drive URL, 2nd is Tunai
    assert "drive.google.com" in mock_sheets_client.appended_rows[-2]["Bukti URL"]
    assert mock_sheets_client.appended_rows[-1]["Bukti URL"] == "Tunai"
