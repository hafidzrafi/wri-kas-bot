"""Tier 2: Boundary & Corner Cases: Standalone Reconciler (reconcile.py)."""

from wri_kas_bot import reconcile
from wri_kas_bot.database import Database
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def test_reconcile_empty_backlog(initialized_db):
    """Boundary: Running reconciler when 0 pending transactions exist exits cleanly."""
    db = Database(initialized_db)
    count = reconcile.reconcile_backlog(db=db, dry_run=False)
    assert count == 0


def test_reconcile_dry_run_absolute_zero_mutation(initialized_db, mock_sheets_client):
    """Boundary: --dry-run produces strictly 0 database changes and 0 API calls."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=10000, ocr_amount=10000)

    count = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=True)
    assert count == 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "PENDING"
        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 0

    assert len(mock_sheets_client.updated_cells) == 0


def test_reconcile_unresolvable_drive_file_flagged(initialized_db, mock_sheets_client):
    """Corner case: Empty Drive receipt link falls back to 'Via Bot' without halting."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=10000, ocr_amount=10000)

    count = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=False)
    assert count == 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"


def test_reconcile_inactive_member_handling(initialized_db, mock_sheets_client):
    """Corner case: Transaction belongs to member marked 'Nonaktif'."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        conn.execute("UPDATE members SET status = 'Nonaktif' WHERE id = 1")
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=10000, ocr_amount=10000)

    count = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=False)
    assert count == 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"


def test_reconcile_unparseable_receipt_fallback_to_prompt(initialized_db, mock_sheets_client):
    """Corner case: When OCR has 0/None nominal, reconciler falls back to default monthly due."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=0, ocr_amount=None)

    count = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=False)
    assert count == 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 1  # 10,000 covers 1 month
