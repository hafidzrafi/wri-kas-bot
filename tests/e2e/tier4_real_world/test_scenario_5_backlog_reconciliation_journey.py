"""Tier 4: Real-World Scenarios: Scenario 5: Backlog Reconciliation Journey."""

from wri_kas_bot import reconcile
from wri_kas_bot.database import Database
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def test_scenario_5_reconcile_stranded_transactions(
    initialized_db,
    mock_drive_client,
    mock_sheets_client,
):
    """Scenario 5: Reconciling legacy backlog transactions with dry-run and apply."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx1 = tx_repo.create_draft(member_id=1, amount_paid=20000, ocr_amount=20000)
        tx2 = tx_repo.create_draft(member_id=2, amount_paid=10000, ocr_amount=10000)

    # 1. Dry Run Mode
    dry_count = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=True)
    assert dry_count == 2
    assert len(mock_sheets_client.updated_cells) == 0

    with db.connect() as conn:
        assert TransactionRepository(conn).get_by_id(tx1).status == "PENDING"
        assert TransactionRepository(conn).get_by_id(tx2).status == "PENDING"

    # 2. Apply Mode
    apply_count = reconcile.reconcile_backlog(
        db=db, sheets_client=mock_sheets_client, dry_run=False
    )
    assert apply_count == 2

    with db.connect() as conn:
        t1 = TransactionRepository(conn).get_by_id(tx1)
        t2 = TransactionRepository(conn).get_by_id(tx2)
        assert t1.status == "APPROVED"
        assert t1.sync_status == "SYNCED"
        assert t2.status == "APPROVED"
        assert t2.sync_status == "SYNCED"

        assert len(MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)) == 2
        assert len(MonthlyDuesRepository(conn).get_paid(member_id=2, year=2026)) == 1

    assert len(mock_sheets_client.updated_cells) == 3
    assert len(mock_sheets_client.appended_rows) == 2
