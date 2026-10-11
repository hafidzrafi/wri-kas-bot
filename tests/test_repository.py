"""Unit tests for domain repositories (src/wri_kas_bot/repository.py)."""

import sqlite3

import pytest

from wri_kas_bot import repository
from wri_kas_bot.repository import (
    MemberRepository,
    MonthlyDuesRepository,
    MonthlySummaryRepository,
    OutboxRepository,
    TransactionRepository,
)


def test_member_crud_and_balance(in_memory_db):
    """Verify MemberRepository CRUD operations and balance updates."""
    repo = MemberRepository(in_memory_db)
    m_id = repo.create(name="Alice", phone_number="6281100000001", balance=5000)
    assert m_id > 0

    member = repo.get_by_phone("6281100000001")
    assert member is not None
    assert member.name == "Alice"
    assert member.balance == 5000
    assert member["name"] == "Alice"  # DictAttrMixin subscripting

    repo.update_balance(m_id, 15000)
    updated = repo.get_by_id(m_id)
    assert updated is not None
    assert updated.balance == 15000

    repo.update_last_billed_at(m_id, "2026-10-01 19:00:00")
    billed = repo.get_by_id(m_id)
    assert billed is not None
    assert "2026-10-01" in str(billed.last_billed_at)

    active = repo.list_active()
    assert len(active) == 1
    assert active[0].name == "Alice"


def test_member_unique_phone_constraint(in_memory_db):
    """Verify duplicate phone insertion raises sqlite3.IntegrityError."""
    repo = MemberRepository(in_memory_db)
    repo.create(name="Bob", phone_number="6281100000002")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(name="Bob Clone", phone_number="6281100000002")


def test_monthly_dues_matrix_lifecycle(in_memory_db):
    """Verify 11-month dues initialization, marking paid, and reverting."""
    m_repo = MemberRepository(in_memory_db)
    d_repo = MonthlyDuesRepository(in_memory_db)

    m_id = m_repo.create(name="Charlie", phone_number="6281100000003")
    d_repo.init_member_dues(m_id, 2026)

    unpaid = d_repo.get_unpaid(m_id, 2026)
    assert len(unpaid) == 11
    assert unpaid[0].month == "Februari"
    assert unpaid[0].month_index == 2
    assert unpaid[-1].month == "Desember"
    assert unpaid[-1].month_index == 12

    # Mark first month paid
    t_repo = TransactionRepository(in_memory_db)
    tx_id = t_repo.create_draft(m_id, 10000, "TRANSFER_JAGO")
    first_due_id = unpaid[0].id
    d_repo.mark_paid(
        first_due_id, tx_id=tx_id, payment_method="TRANSFER_JAGO", proof_url="drive://url1"
    )

    paid = d_repo.get_paid(m_id, 2026)
    assert len(paid) == 1
    assert paid[0].month == "Februari"
    assert paid[0].transaction_id == tx_id

    remaining_unpaid = d_repo.get_unpaid(m_id, 2026)
    assert len(remaining_unpaid) == 10

    # Revert transaction
    reverted = d_repo.revert_to_unpaid(tx_id)
    assert len(reverted) == 1
    assert len(d_repo.get_unpaid(m_id, 2026)) == 11


def test_transaction_lifecycle_and_queries(in_memory_db):
    """Verify transaction creation, approval, rejection, and outbox sync querying."""
    m_repo = MemberRepository(in_memory_db)
    t_repo = TransactionRepository(in_memory_db)

    m_id = m_repo.create(name="Dave", phone_number="6281100000004")
    tx_id = t_repo.create_draft(
        member_id=m_id,
        amount_paid=20000,
        payment_method="TRANSFER_JAGO",
        wa_message_id="WA_12345",
    )
    assert tx_id > 0

    tx = t_repo.get_by_id(tx_id)
    assert tx is not None
    assert tx.status == "PENDING"
    assert tx.amount_paid == 20000

    # Approve transaction
    t_repo.approve(
        tx_id=tx_id,
        balance_before=0,
        balance_after=0,
        months_filled=["Februari", "Maret"],
        approved_by="6281100000001",
        sheets_cells_filled=[{"sheet": "PENGURUS-2025/2026", "cell": "C4"}],
    )

    approved = t_repo.get_by_id(tx_id)
    assert approved is not None
    assert approved.status == "APPROVED"
    assert approved.sync_status == "PENDING"
    assert approved.approved_by == "6281100000001"
    assert len(approved.months_filled) == 2

    # Query latest approved
    latest = t_repo.get_latest_approved_for_member(m_id)
    assert latest is not None
    assert latest.id == tx_id

    # Pending sync query
    pending_sync = t_repo.get_pending_sync()
    assert len(pending_sync) >= 1
    assert pending_sync[0].id == tx_id

    # Update sync status
    t_repo.update_sync_status(tx_id, "SYNCED", proof_drive_url="https://drive.google.com/test")
    synced = t_repo.get_by_id(tx_id)
    assert synced is not None
    assert synced.sync_status == "SYNCED"
    assert synced.proof_drive_url == "https://drive.google.com/test"
    assert synced.retry_count == 1


def test_outbox_repository_queue(in_memory_db):
    """Verify OutboxRepository enqueue, processing, and failure counting."""
    repo = OutboxRepository(in_memory_db)
    task_id = repo.enqueue(
        transaction_id=None,
        task_type="SYNC_DRIVE",
        payload={"file_name": "receipt.jpg"},
    )
    assert task_id > 0

    pending = repo.get_pending()
    assert len(pending) == 1
    assert pending[0].task_type == "SYNC_DRIVE"
    assert pending[0].payload["file_name"] == "receipt.jpg"

    repo.mark_failed(task_id, error="Network timeout", max_retries=3)
    pending_retry = repo.get_pending()
    assert len(pending_retry) == 1
    assert pending_retry[0].retry_count == 1
    assert pending_retry[0].last_error == "Network timeout"

    repo.mark_completed(task_id)
    assert len(repo.get_pending()) == 0


def test_monthly_summary_repository(in_memory_db):
    """Verify MonthlySummaryRepository creation and sync state."""
    repo = MonthlySummaryRepository(in_memory_db)
    summary_id = repo.create(
        period_month="Februari 2026",
        non_cash_amount=100000,
        cash_amount=20000,
        prev_balance=50000,
        new_balance=170000,
        target_row=55,
    )
    assert summary_id > 0

    pending = repo.get_pending_sync()
    assert len(pending) == 1
    assert pending[0].period_month == "Februari 2026"

    repo.mark_synced(summary_id)
    assert len(repo.get_pending_sync()) == 0


def test_module_level_helpers(in_memory_db):
    """Verify functional module-level repository wrappers."""
    m_repo = MemberRepository(in_memory_db)
    m_id = m_repo.create(name="Eve", phone_number="6281100000005", balance=10000)

    member = repository.get_member_by_phone(in_memory_db, "6281100000005")
    assert member is not None
    assert member.name == "Eve"

    repository.update_member_balance(in_memory_db, m_id, 25000)
    assert repository.get_member_by_phone(in_memory_db, "6281100000005").balance == 25000

    tx_id = repository.create_transaction(in_memory_db, m_id, 10000, status="PENDING")
    assert tx_id > 0

    pending = repository.get_transactions_by_status(in_memory_db, "PENDING")
    assert len(pending) >= 1

    repository.update_transaction_sync_status(in_memory_db, tx_id, "SYNCED")
    tx = repository.get_transaction_by_id(in_memory_db, tx_id)
    assert tx is not None
    assert tx.sync_status == "SYNCED"
