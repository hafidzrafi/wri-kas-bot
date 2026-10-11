"""Unit tests for atomic Undo & Koreksi engine (src/wri_kas_bot/undo.py)."""

import json
import sqlite3
import threading

import pytest

from wri_kas_bot.database import SCHEMA_SQL, init_db
from wri_kas_bot.repository import (
    MemberRepository,
    MonthlyDuesRepository,
    TransactionRepository,
)
from wri_kas_bot.undo import (
    InvalidTransactionStateError,
    SupersededTransactionError,
    TransactionNotFoundError,
    execute_koreksi,
    execute_undo,
)


def test_undo_restores_balance_and_reopens_months(seeded_db):
    """Verify normal rollback restores balance_before, reopens monthly dues, and extracts cells."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    # Alice pays Rp 20.000
    tx_id = t_repo.create_draft(alice_id, 20000, "TRANSFER_JAGO")
    t_repo.approve(
        tx_id=tx_id,
        balance_before=0,
        balance_after=0,
        months_filled=["Februari", "Maret"],
        approved_by="6281100000001",
        sheets_cells_filled=[{"sheet": "PENGURUS-2025/2026", "cell": "C3"}],
    )
    unpaid = d_repo.get_unpaid(alice_id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id, "TRANSFER_JAGO", "drive://url1")
    d_repo.mark_paid(unpaid[1].id, tx_id, "TRANSFER_JAGO", "drive://url2")

    # Verify state prior to undo
    assert len(d_repo.get_unpaid(alice_id, 2026)) == 9

    # Execute Undo
    result = execute_undo(conn, tx_id)
    assert result.success is True
    assert result.restored_balance == 0
    assert result.reverted_months == [(2026, 2), (2026, 3)]
    assert result.sheets_cells_to_clear == ["'PENGURUS-2025/2026'!C3"]

    # Verify database state after undo
    tx = t_repo.get_by_id(tx_id)
    assert tx is not None
    assert tx.status == "REVERTED"
    assert tx.sync_status == "PENDING"

    alice_after = m_repo.get_by_id(alice_id)
    assert alice_after is not None
    assert alice_after.balance == 0
    assert len(d_repo.get_unpaid(alice_id, 2026)) == 11


def test_undo_restores_positive_initial_balance(seeded_db):
    """Verify rollback restores positive deposit balance before payment."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    bob = m_repo.get_by_phone("6281100000004")
    assert bob is not None
    bob_id = bob.id
    assert bob.balance == 5000

    tx_id = t_repo.create_draft(bob_id, 25000, "TRANSFER_JAGO")
    t_repo.approve(
        tx_id=tx_id,
        balance_before=5000,
        balance_after=0,
        months_filled=["Februari", "Maret", "April"],
        approved_by="6281100000001",
    )
    m_repo.update_balance(bob_id, 0)

    unpaid = d_repo.get_unpaid(bob_id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id, "TRANSFER_JAGO", "drive://url")
    d_repo.mark_paid(unpaid[1].id, tx_id, "TRANSFER_JAGO", "drive://url")
    d_repo.mark_paid(unpaid[2].id, tx_id, "TRANSFER_JAGO", "drive://url")

    # Rollback
    result = execute_undo(conn, tx_id)
    assert result.success is True
    assert result.restored_balance == 5000

    bob_after = m_repo.get_by_id(bob_id)
    assert bob_after is not None
    assert bob_after.balance == 5000


def test_reject_undo_if_not_latest_approved_transaction(seeded_db):
    """Invariant: Cannot undo Tx #1 if Tx #2 is already approved for the same member."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    tx1_id = t_repo.create_draft(alice_id, 10000)
    t_repo.approve(tx1_id, 0, 0, ["Februari"], "6281100000001")

    tx2_id = t_repo.create_draft(alice_id, 10000)
    t_repo.approve(tx2_id, 0, 0, ["Maret"], "6281100000001")

    # Attempt to undo Tx 1 must raise SupersededTransactionError
    with pytest.raises(SupersededTransactionError):
        execute_undo(conn, tx1_id)

    # Tx 1 status must remain APPROVED
    tx1 = t_repo.get_by_id(tx1_id)
    assert tx1 is not None
    assert tx1.status == "APPROVED"


def test_reject_undo_non_approved_transaction(seeded_db):
    """Verify undo rejects PENDING, REJECTED, or REVERTED transactions."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    tx_pending = t_repo.create_draft(alice_id, 10000)
    with pytest.raises(InvalidTransactionStateError):
        execute_undo(conn, tx_pending)

    t_repo.reject(tx_pending, "Fake receipt", "6281100000001")
    with pytest.raises(InvalidTransactionStateError):
        execute_undo(conn, tx_pending)


def test_reject_undo_nonexistent_transaction(seeded_db):
    """Verify undo raises TransactionNotFoundError on non-existent transaction ID."""
    with pytest.raises(TransactionNotFoundError):
        execute_undo(seeded_db, 999999)


def test_undo_zero_month_deposit_underpayment(seeded_db):
    """Verify undo of partial deposit payment with 0 cleared months."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    tx_id = t_repo.create_draft(alice_id, 5000)
    t_repo.approve(
        tx_id, balance_before=0, balance_after=5000, months_filled=[], approved_by="6281100000001"
    )
    m_repo.update_balance(alice_id, 5000)

    result = execute_undo(conn, tx_id)
    assert result.restored_balance == 0
    assert result.reverted_months == []
    assert result.sheets_cells_to_clear == []

    alice_after = m_repo.get_by_id(alice_id)
    assert alice_after is not None
    assert alice_after.balance == 0


def test_koreksi_increases_payment_amount(seeded_db):
    """Verify /koreksi atomically adjusts transaction from Rp 10.000 to Rp 20.000."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    tx_id = t_repo.create_draft(alice_id, 10000)
    t_repo.approve(tx_id, 0, 0, ["Februari"], "6281100000001")
    unpaid = d_repo.get_unpaid(alice_id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id, "TRANSFER_JAGO", "drive://url")

    # Correct to Rp 20.000
    res = execute_koreksi(conn, tx_id, new_amount=20000, approved_by="6281100000001")
    assert res.success is True
    assert res.new_amount == 20000
    assert len(res.new_months_filled) == 2

    # Now 2 months paid (Februari & Maret), 9 unpaid
    assert len(d_repo.get_unpaid(alice_id, 2026)) == 9
    tx = t_repo.get_by_id(tx_id)
    assert tx is not None
    assert tx.amount_paid == 20000
    assert tx.status == "APPROVED"


def test_koreksi_decreases_payment_amount_to_underpayment(seeded_db):
    """Verify /koreksi decreases from Rp 20.000 to Rp 5.000,
    reopening months and accumulating deposit."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    tx_id = t_repo.create_draft(alice_id, 20000)
    t_repo.approve(tx_id, 0, 0, ["Februari", "Maret"], "6281100000001")
    unpaid = d_repo.get_unpaid(alice_id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id, "TRANSFER_JAGO", "drive://url")
    d_repo.mark_paid(unpaid[1].id, tx_id, "TRANSFER_JAGO", "drive://url")

    # Correct to Rp 5.000
    res = execute_koreksi(conn, tx_id, new_amount=5000, approved_by="6281100000001")
    assert res.success is True
    assert res.new_amount == 5000
    assert len(res.new_months_filled) == 0
    assert res.new_balance == 5000

    # All 11 months are unpaid again
    assert len(d_repo.get_unpaid(alice_id, 2026)) == 11
    alice_after = m_repo.get_by_id(alice_id)
    assert alice_after is not None
    assert alice_after.balance == 5000


def test_undo_with_raw_sqlite_connection():
    """Verify execute_undo succeeds on standard sqlite3.connect() without sqlite3.Row."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO members (id, name, phone_number, balance) "
        "VALUES (1, 'Alice', '6281100000001', 0)"
    )
    conn.execute(
        "INSERT INTO transactions "
        "(id, member_id, amount_paid, payment_method, status, balance_before, balance_after) "
        "VALUES (1, 1, 10000, 'TRANSFER_JAGO', 'APPROVED', 0, 0)"
    )

    result = execute_undo(conn, 1)
    assert result.success is True
    assert result.transaction_id == 1


def test_koreksi_persists_coordinate_tuples_in_db(seeded_db):
    """Verify execute_koreksi stores [(year, month_idx), ...] in transactions.months_filled."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    tx_id = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx_id, 0, 0, [(2026, 2)], "6281100000001")
    unpaid = d_repo.get_unpaid(alice.id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id, "TRANSFER_JAGO", "drive://url")

    # Correct to 2 months
    res = execute_koreksi(conn, tx_id, new_amount=20000, approved_by="6281100000001")
    assert res.success is True

    # Inspect raw JSON in database
    cur = conn.cursor()
    cur.execute("SELECT months_filled FROM transactions WHERE id = ?", (tx_id,))
    raw_val = cur.fetchone()["months_filled"]
    parsed = json.loads(raw_val)

    # Must be coordinate tuples [[2026, 2], [2026, 3]], not string names
    assert parsed == [[2026, 2], [2026, 3]]


def test_undo_and_koreksi_inside_db_transaction_context_manager(tmp_path):
    """Verify execute_undo and execute_koreksi work seamlessly within db.transaction()."""
    db_path = str(tmp_path / "tx_mgr.db")
    db = init_db(db_path)

    with db.connect() as conn:
        m_repo = MemberRepository(conn)
        d_repo = MonthlyDuesRepository(conn)
        t_repo = TransactionRepository(conn)

        alice_id = m_repo.create("Alice", "6281100000001", balance=0)
        d_repo.init_member_dues(alice_id, 2026)
        tx_id = t_repo.create_draft(alice_id, 10000)
        t_repo.approve(tx_id, 0, 0, ["Februari"], "admin")
        d_repo.mark_paid(d_repo.get_unpaid(alice_id, 2026)[0].id, tx_id)

    # execute_koreksi inside db.transaction()
    with db.transaction() as conn:
        k_res = execute_koreksi(conn, tx_id, new_amount=20000, approved_by="admin")
        assert k_res.success is True

    # execute_undo inside db.transaction()
    with db.transaction() as conn:
        u_res = execute_undo(conn, tx_id)
        assert u_res.success is True


def test_concurrent_double_undo_serialization(tmp_path):
    """Verify concurrent double-undo calls serialize so exactly one succeeds."""
    db_path = str(tmp_path / "undo_race.db")
    db = init_db(db_path)

    with db.connect() as conn:
        m_repo = MemberRepository(conn)
        d_repo = MonthlyDuesRepository(conn)
        t_repo = TransactionRepository(conn)

        alice_id = m_repo.create("Alice", "6281100000001", balance=0)
        d_repo.init_member_dues(alice_id, 2026)
        tx_id = t_repo.create_draft(alice_id, 10000)
        t_repo.approve(tx_id, 0, 0, ["Februari"], "admin")
        d_repo.mark_paid(d_repo.get_unpaid(alice_id, 2026)[0].id, tx_id)

    results: list[dict] = []
    barrier = threading.Barrier(2)

    def undo_worker(worker_id: int):
        conn = sqlite3.connect(db_path, timeout=30.0, autocommit=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")

        barrier.wait()
        try:
            res = execute_undo(conn, tx_id, raise_on_error=True)
            results.append({"worker": worker_id, "success": True, "res": res})
        except Exception as e:
            results.append({"worker": worker_id, "success": False, "error": str(e)})
        finally:
            conn.close()

    t1 = threading.Thread(target=undo_worker, args=(1,))
    t2 = threading.Thread(target=undo_worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]
    assert len(successes) == 1
    assert len(failures) == 1
