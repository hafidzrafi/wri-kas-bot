"""Tier 3: Cross-Feature Interactions — Approval then Undo Rollback with Sheets BatchClear."""

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository
from wri_kas_bot.undo import execute_undo


@pytest.mark.asyncio
async def test_flow_approval_then_undo_batchclear_sheets(
    initialized_db, mock_sheets_client, mock_drive_client
):
    """Pairwise: Approved dues synced to Sheets -> /undo issued -> BatchClear clears exact cells."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)

        tx_id = tx_repo.create_draft(member_id=1, amount_paid=20000, ocr_amount=20000)
        dues_repo.mark_paid(1, transaction_id=tx_id)
        dues_repo.mark_paid(2, transaction_id=tx_id)

        tx_repo.approve(
            tx_id=tx_id,
            balance_before=0,
            balance_after=0,
            months_filled=["Februari", "Maret"],
            approved_by="6281100000099@s.whatsapp.net",
            sheets_cells_filled=["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!E5"],
        )

    # 1. Execute Undo
    with db.connect() as conn:
        undo_res = execute_undo(conn, transaction_id=tx_id, raise_on_error=True)
        assert undo_res.success is True
        assert "PENGURUS-2025/2026!D5" in undo_res.sheets_cells_to_clear
        assert "PENGURUS-2025/2026!E5" in undo_res.sheets_cells_to_clear

    # 2. Run Outbox Worker to process REVERTED transaction
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1

    # Verify mock_sheets_client received batch_clear_cells
    assert "PENGURUS-2025/2026!D5" in mock_sheets_client.cleared_ranges
    assert "PENGURUS-2025/2026!E5" in mock_sheets_client.cleared_ranges


@pytest.mark.asyncio
async def test_flow_undo_rejected_if_balance_corrupted(initialized_db):
    """Pairwise: /undo rejected if member balance was mutated by newer transactions."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)

        tx1_id = tx_repo.create_draft(member_id=1, amount_paid=10000)
        tx_repo.approve(
            tx_id=tx1_id,
            balance_before=0,
            balance_after=0,
            months_filled=["Februari"],
            approved_by="treasurer",
        )

        tx2_id = tx_repo.create_draft(member_id=1, amount_paid=10000)
        tx_repo.approve(
            tx_id=tx2_id,
            balance_before=0,
            balance_after=0,
            months_filled=["Maret"],
            approved_by="treasurer",
        )

    with db.connect() as conn:
        res = execute_undo(conn, transaction_id=tx1_id, raise_on_error=False)
        assert res.success is False
        assert "subsequent approved transaction exists" in res.message
