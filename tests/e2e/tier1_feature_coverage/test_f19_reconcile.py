"""Tier 1: Feature Coverage — Feature 19: Standalone Reconciler CLI (reconcile.py)."""

from wri_kas_bot import reconcile
from wri_kas_bot.database import Database
from wri_kas_bot.repository import MonthlyDuesRepository, TransactionRepository


def test_reconcile_finds_stranded_transactions(initialized_db):
    """Verify reconciler identifies un-synced transactions from prototype backlog."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_repo.create_draft(member_id=1, amount_paid=20000, ocr_amount=20000)

    candidates = reconcile.find_stranded_transactions(db)
    assert len(candidates) >= 1
    assert candidates[0]["member_name"] == "Alice"
    assert candidates[0]["amount_paid"] == 20000


def test_reconcile_dry_run_mode_no_mutations(initialized_db, mock_sheets_client):
    """Verify --dry-run produces no side-effects in database or Google Sheets."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=10000, ocr_amount=10000)

    processed = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=True)
    assert processed >= 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "PENDING"
        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 0

    assert len(mock_sheets_client.updated_cells) == 0


def test_reconcile_apply_mode_allocates_dues(initialized_db, mock_sheets_client):
    """Verify --apply mode commits transactions and clears corresponding dues."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=1, amount_paid=20000, ocr_amount=20000)

    processed = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=False)
    assert processed >= 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        assert tx.sync_status == "SYNCED"

        dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(dues) == 2
        assert [d.month for d in dues] == ["Februari", "Maret"]

    assert len(mock_sheets_client.updated_cells) == 2


def test_reconcile_resolves_ocr_metadata(initialized_db, mock_sheets_client):
    """Verify reconciler queries OCR for stranded receipts with missing nominals."""
    db = Database(initialized_db)
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        tx_id = tx_repo.create_draft(member_id=2, amount_paid=0, ocr_amount=30000)

    processed = reconcile.reconcile_backlog(db=db, sheets_client=mock_sheets_client, dry_run=False)
    assert processed >= 1

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        dues = MonthlyDuesRepository(conn).get_paid(member_id=2, year=2026)
        assert len(dues) == 3


def test_reconcile_cli_exit_code_zero(initialized_db):
    """Verify CLI entrypoint returns exit code 0 when finished."""
    ret = reconcile.main(["--dry-run", f"--db-path={initialized_db}"])
    assert ret == 0
