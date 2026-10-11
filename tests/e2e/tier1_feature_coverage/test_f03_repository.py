"""Tier 1: Feature Coverage — Feature 3: Domain Repositories (repository.py)."""

import sqlite3

import pytest

try:
    from wri_kas_bot import repository

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    repository = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.repository not implemented yet")


def test_member_repository_create_and_find_by_phone(initialized_db):
    """Verify repository creates and retrieves member by phone number."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    if hasattr(repository, "MemberRepository"):
        repo = repository.MemberRepository(conn)
        member = repo.find_by_phone("6281100000001")
    else:
        member = repository.get_member_by_phone(conn, "6281100000001")
    conn.close()

    assert member is not None
    assert member.name == "Alice"
    assert member.phone_number == "6281100000001"


def test_member_repository_update_balance(initialized_db):
    """Verify updating member balance persists in database."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    if hasattr(repository, "MemberRepository"):
        repo = repository.MemberRepository(conn)
        member = repo.find_by_phone("6281100000001")
        repo.update_balance(member.id, 15000)
        updated = repo.find_by_phone("6281100000001")
    else:
        member = repository.get_member_by_phone(conn, "6281100000001")
        repository.update_member_balance(conn, member.id, 15000)
        updated = repository.get_member_by_phone(conn, "6281100000001")
    conn.close()

    assert updated.balance == 15000


def test_monthly_due_repository_get_unpaid(initialized_db):
    """Verify getting chronological unpaid dues for a member."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    if hasattr(repository, "MonthlyDueRepository"):
        repo = repository.MonthlyDueRepository(conn)
        unpaid = repo.get_unpaid_months(1)
    else:
        unpaid = repository.get_unpaid_months(conn, 1)
    conn.close()

    assert len(unpaid) == 11
    first_month = unpaid[0]
    assert (
        "Februari" in str(first_month)
        or getattr(first_month, "month", None) == "Februari"
        or (isinstance(first_month, tuple | list) and first_month[1] == "Februari")
    )


def test_transaction_repository_insert_and_get_pending(initialized_db):
    """Verify inserting transaction and querying by status."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    if hasattr(repository, "TransactionRepository"):
        repo = repository.TransactionRepository(conn)
        tx_id = repo.create_transaction(
            member_id=1,
            amount_paid=10000,
            payment_method="TRANSFER_JAGO",
            balance_before=0,
            balance_after=0,
            status="PENDING",
        )
        pending = repo.get_by_status("PENDING")
    else:
        tx_id = repository.create_transaction(
            conn,
            member_id=1,
            amount_paid=10000,
            payment_method="TRANSFER_JAGO",
            balance_before=0,
            balance_after=0,
            status="PENDING",
        )
        pending = repository.get_transactions_by_status(conn, "PENDING")
    conn.close()

    assert tx_id is not None
    assert len(pending) >= 1


def test_transaction_repository_update_sync_status(initialized_db):
    """Verify updating sync status and sheets cell mappings."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, status) "
        "VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, 'APPROVED')"
    )
    tx_id = cursor.lastrowid
    conn.commit()

    if hasattr(repository, "TransactionRepository"):
        repo = repository.TransactionRepository(conn)
        repo.update_sync_status(tx_id, "SYNCED")
        tx = repo.get_by_id(tx_id)
    else:
        repository.update_transaction_sync_status(conn, tx_id, "SYNCED")
        tx = repository.get_transaction_by_id(conn, tx_id)
    conn.close()

    assert tx.sync_status == "SYNCED"


def test_member_repository_update_last_billed_at(initialized_db):
    """Verify recording billing blast timestamp."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    if hasattr(repository, "MemberRepository"):
        repo = repository.MemberRepository(conn)
        repo.update_last_billed_at(1, "2026-10-01T19:00:00Z")
        member = repo.find_by_phone("6281100000001")
    else:
        repository.update_member_last_billed_at(conn, 1, "2026-10-01T19:00:00Z")
        member = repository.get_member_by_phone(conn, "6281100000001")
    conn.close()

    assert member.last_billed_at is not None
    assert "2026-10-01" in str(member.last_billed_at)
