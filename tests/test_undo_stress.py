"""Tests for undo and koreksi concurrency and edge cases."""

import sqlite3
import threading
import time

import pytest

from wri_kas_bot.database import init_db
from wri_kas_bot.repository import (
    MemberRepository,
    MonthlyDuesRepository,
    TransactionRepository,
)
from wri_kas_bot.undo import (
    InvalidTransactionStateError,
    SupersededTransactionError,
    execute_koreksi,
    execute_undo,
)


@pytest.fixture
def temp_db_path(tmp_path):
    """Provide a real SQLite file path for WAL mode testing."""
    return str(tmp_path / "kas_stress.db")


def test_sequential_double_undo_fails_gracefully(seeded_db):
    """Empirical check: Calling /undo twice on the same transaction must fail on second call."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    tx_id = t_repo.create_draft(alice_id, 10000, "TRANSFER_JAGO")
    t_repo.approve(
        tx_id=tx_id,
        balance_before=0,
        balance_after=0,
        months_filled=["Februari"],
        approved_by="6281100000001",
    )
    unpaid = d_repo.get_unpaid(alice_id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id, "TRANSFER_JAGO", "drive://url1")

    # 1st Undo: must succeed
    res1 = execute_undo(conn, tx_id, raise_on_error=True)
    assert res1.success is True
    assert res1.reverted_months == [(2026, 2)]

    # 2nd Undo with raise_on_error=True: must raise InvalidTransactionStateError
    with pytest.raises(InvalidTransactionStateError) as exc_info:
        execute_undo(conn, tx_id, raise_on_error=True)
    assert "REVERTED" in str(exc_info.value)

    # 2nd Undo with raise_on_error=False: must return success=False
    res2 = execute_undo(conn, tx_id, raise_on_error=False)
    assert res2.success is False
    assert "REVERTED" in res2.message


def test_out_of_order_undo_blocked_by_chronological_guard(seeded_db):
    """Empirical check: Tx 1 cannot be undone while newer Tx 2 is approved."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None
    alice_id = alice.id

    # Tx 1: Feb
    tx1_id = t_repo.create_draft(alice_id, 10000)
    t_repo.approve(tx1_id, 0, 0, ["Februari"], "admin")
    unpaid1 = d_repo.get_unpaid(alice_id, 2026)
    d_repo.mark_paid(unpaid1[0].id, tx1_id, "TRANSFER_JAGO", "drive://url1")

    # Tx 2: Mar
    tx2_id = t_repo.create_draft(alice_id, 10000)
    t_repo.approve(tx2_id, 0, 0, ["Maret"], "admin")
    unpaid2 = d_repo.get_unpaid(alice_id, 2026)
    d_repo.mark_paid(unpaid2[0].id, tx2_id, "TRANSFER_JAGO", "drive://url2")

    # Attempt undo on Tx 1 must be rejected
    with pytest.raises(SupersededTransactionError) as exc_info:
        execute_undo(conn, tx1_id, raise_on_error=True)
    assert f"subsequent approved transaction exists for member #{alice_id}" in str(exc_info.value)

    res_soft = execute_undo(conn, tx1_id, raise_on_error=False)
    assert res_soft.success is False

    # Tx 1 remains APPROVED
    tx1 = t_repo.get_by_id(tx1_id)
    assert tx1 is not None and tx1.status == "APPROVED"

    # LIFO rollback: Undo Tx 2 first -> must succeed
    res_undo2 = execute_undo(conn, tx2_id, raise_on_error=True)
    assert res_undo2.success is True

    # Now Tx 1 is the latest approved -> Undo Tx 1 must succeed
    res_undo1 = execute_undo(conn, tx1_id, raise_on_error=True)
    assert res_undo1.success is True
    assert len(d_repo.get_unpaid(alice_id, 2026)) == 11


def test_undo_isolation_between_different_members(seeded_db):
    """Empirical check: Tx for Bob does NOT block undo for Alice even if Bob's tx has higher ID."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    bob = m_repo.get_by_phone("6281100000004")
    assert alice is not None and bob is not None

    # Alice Tx 1
    tx1_alice = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx1_alice, 0, 0, ["Februari"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(alice.id, 2026)[0].id, tx1_alice)

    # Bob Tx 2 (higher ID)
    tx2_bob = t_repo.create_draft(bob.id, 10000)
    t_repo.approve(tx2_bob, 5000, 5000, ["Februari"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(bob.id, 2026)[0].id, tx2_bob)

    assert tx2_bob > tx1_alice

    # Undoing Alice's Tx 1 must NOT be blocked by Bob's Tx 2
    res = execute_undo(conn, tx1_alice, raise_on_error=True)
    assert res.success is True
    assert res.transaction_id == tx1_alice


def test_undo_not_blocked_by_pending_or_rejected_or_reverted_newer_transactions(seeded_db):
    """Empirical check: Only APPROVED subsequent transactions block undo."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    # Tx 1: Approved
    tx1_id = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx1_id, 0, 0, ["Februari"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(alice.id, 2026)[0].id, tx1_id)

    # Tx 2: Newer, but PENDING
    tx2_id = t_repo.create_draft(alice.id, 10000)
    assert tx2_id > tx1_id

    # Undoing Tx 1 must succeed despite newer PENDING tx
    res1 = execute_undo(conn, tx1_id)
    assert res1.success is True

    # Re-approve Tx 1 for another test
    t_repo.approve(tx1_id, 0, 0, ["Februari"], "admin")

    # Tx 3: Newer, but REJECTED
    tx3_id = t_repo.create_draft(alice.id, 10000)
    t_repo.reject(tx3_id, "Bad receipt", "admin")

    # Undoing Tx 1 must succeed despite newer REJECTED tx
    res2 = execute_undo(conn, tx1_id)
    assert res2.success is True


def test_undo_non_approved_transactions_fails(seeded_db):
    """Empirical check: Attempting undo on PENDING, REJECTED, or REVERTED must fail."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    # PENDING
    tx_pending = t_repo.create_draft(alice.id, 10000)
    with pytest.raises(InvalidTransactionStateError):
        execute_undo(conn, tx_pending)
    res_pend = execute_undo(conn, tx_pending, raise_on_error=False)
    assert res_pend.success is False

    # REJECTED
    t_repo.reject(tx_pending, "Declined", "admin")
    with pytest.raises(InvalidTransactionStateError):
        execute_undo(conn, tx_pending)
    res_rej = execute_undo(conn, tx_pending, raise_on_error=False)
    assert res_rej.success is False


def test_koreksi_higher_amount_exact_recalculation(seeded_db):
    """Empirical check: /koreksi from 10k to 25k fills 2 months with 5k deposit."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    tx_id = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx_id, 0, 0, ["Februari"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(alice.id, 2026)[0].id, tx_id)

    res = execute_koreksi(conn, tx_id, new_amount=25000, approved_by="treasurer_2")
    assert res.success is True
    assert res.new_amount == 25000
    assert res.new_balance == 5000
    assert len(res.new_months_filled) == 2
    assert res.new_months_filled == [(2026, 2), (2026, 3)]

    # DB verification
    alice_post = m_repo.get_by_id(alice.id)
    assert alice_post is not None and alice_post.balance == 5000
    assert len(d_repo.get_unpaid(alice.id, 2026)) == 9
    assert len(d_repo.get_paid(alice.id, 2026)) == 2

    tx_post = t_repo.get_by_id(tx_id)
    assert tx_post is not None
    assert tx_post.status == "APPROVED"
    assert tx_post.amount_paid == 25000
    assert tx_post.balance_after == 5000


def test_koreksi_lower_amount_reopens_months(seeded_db):
    """Empirical check: /koreksi from 30k to 10k reopens 2 months and resets balance."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    tx_id = t_repo.create_draft(alice.id, 30000)
    t_repo.approve(tx_id, 0, 0, ["Februari", "Maret", "April"], "admin")
    unpaid = d_repo.get_unpaid(alice.id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id)
    d_repo.mark_paid(unpaid[1].id, tx_id)
    d_repo.mark_paid(unpaid[2].id, tx_id)

    assert len(d_repo.get_paid(alice.id, 2026)) == 3

    res = execute_koreksi(conn, tx_id, new_amount=10000, approved_by="treasurer_2")
    assert res.success is True
    assert res.new_amount == 10000
    assert res.new_balance == 0
    assert len(res.new_months_filled) == 1

    assert len(d_repo.get_paid(alice.id, 2026)) == 1
    assert len(d_repo.get_unpaid(alice.id, 2026)) == 10


def test_koreksi_to_zero_amount(seeded_db):
    """Empirical check: /koreksi from 20k to 0 reopens all months and restores zero balance."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    tx_id = t_repo.create_draft(alice.id, 20000)
    t_repo.approve(tx_id, 0, 0, ["Februari", "Maret"], "admin")
    unpaid = d_repo.get_unpaid(alice.id, 2026)
    d_repo.mark_paid(unpaid[0].id, tx_id)
    d_repo.mark_paid(unpaid[1].id, tx_id)

    res = execute_koreksi(conn, tx_id, new_amount=0, approved_by="admin")
    assert res.success is True
    assert res.new_amount == 0
    assert res.new_balance == 0
    assert len(res.new_months_filled) == 0

    assert len(d_repo.get_unpaid(alice.id, 2026)) == 11
    assert len(d_repo.get_paid(alice.id, 2026)) == 0


def test_koreksi_negative_amount_raises(seeded_db):
    """Empirical check: /koreksi with negative amount raises ValueError."""
    with pytest.raises(ValueError, match="new_amount cannot be negative"):
        execute_koreksi(seeded_db, 1, new_amount=-5000, approved_by="admin")


def test_koreksi_out_of_order_blocked(seeded_db):
    """Empirical check: /koreksi on older tx is blocked when newer approved tx exists."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    tx1 = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx1, 0, 0, ["Februari"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(alice.id, 2026)[0].id, tx1)

    tx2 = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx2, 0, 0, ["Maret"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(alice.id, 2026)[0].id, tx2)

    with pytest.raises(SupersededTransactionError):
        execute_koreksi(conn, tx1, new_amount=20000, approved_by="admin")


def test_double_koreksi_successive(seeded_db):
    """Empirical check: Successive corrections on the latest transaction succeed."""
    conn = seeded_db
    m_repo = MemberRepository(conn)
    d_repo = MonthlyDuesRepository(conn)
    t_repo = TransactionRepository(conn)

    alice = m_repo.get_by_phone("6281100000003")
    assert alice is not None

    tx_id = t_repo.create_draft(alice.id, 10000)
    t_repo.approve(tx_id, 0, 0, ["Februari"], "admin")
    d_repo.mark_paid(d_repo.get_unpaid(alice.id, 2026)[0].id, tx_id)

    # 1st koreksi: 10k -> 20k
    res1 = execute_koreksi(conn, tx_id, 20000, "admin")
    assert res1.success is True
    assert len(res1.new_months_filled) == 2

    # 2nd koreksi: 20k -> 30k
    res2 = execute_koreksi(conn, tx_id, 30000, "admin")
    assert res2.success is True
    assert len(res2.new_months_filled) == 3
    assert len(d_repo.get_paid(alice.id, 2026)) == 3


def test_concurrent_writes_prevent_deadlock_with_begin_immediate(temp_db_path):
    """Stress test: 10 concurrent threads issuing write transactions.

    Verifies BEGIN IMMEDIATE in Database.transaction() prevents lock upgrade deadlocks.
    """
    db = init_db(temp_db_path)
    errors: list[str] = []
    success_count = [0]
    lock = threading.Lock()

    def worker_insert(thread_idx: int, iterations: int):
        for i in range(iterations):
            try:
                with db.transaction() as conn:
                    conn.execute(
                        "INSERT INTO members (name, phone_number) VALUES (?, ?)",
                        (f"User_{thread_idx}_{i}", f"62819{thread_idx:02d}{i:04d}"),
                    )
                with lock:
                    success_count[0] += 1
            except Exception as e:
                with lock:
                    errors.append(f"T{thread_idx} iter {i}: {type(e).__name__} - {e}")

    threads = [threading.Thread(target=worker_insert, args=(t, 15)) for t in range(10)]
    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = time.time() - start_time

    assert not errors, f"Encountered write errors/deadlocks: {errors[:5]}"
    assert success_count[0] == 150
    assert duration < 10.0, f"Concurrent execution took too long: {duration:.2f}s"


def test_concurrent_double_undo_race_condition(temp_db_path):
    """Stress test: Two concurrent threads execute undo on the exact same transaction.

    Invariant: Exactly ONE thread must succeed; the other MUST fail.
    Two successes on the same transaction is a race condition ledger corruption bug.
    """
    db = init_db(temp_db_path)

    # Setup: Alice has 1 approved transaction
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
        conn = sqlite3.connect(temp_db_path, timeout=30.0, autocommit=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")

        barrier.wait()  # Synchronize to trigger exact simultaneous execution
        try:
            res = execute_undo(conn, tx_id, raise_on_error=True)
            results.append({"worker": worker_id, "success": True, "result": res})
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

    # Invariant: Double undo must never result in 2 successes!
    assert len(successes) == 1, (
        f"Race condition detected! Both threads succeeded: {results}. "
        "execute_undo must serialize access so that only one undo can succeed."
    )
    assert len(failures) == 1


def test_execute_undo_inside_db_transaction_context_manager(temp_db_path):
    """Adversarial check: execute_undo must work seamlessly inside Database.transaction().

    If execute_undo prematurely commits via conn.execute('COMMIT;'), exiting
    Database.transaction() will crash with:
    'OperationalError: cannot commit - no transaction is active'
    """
    db = init_db(temp_db_path)

    with db.connect() as conn:
        m_repo = MemberRepository(conn)
        d_repo = MonthlyDuesRepository(conn)
        t_repo = TransactionRepository(conn)

        alice_id = m_repo.create("Alice", "6281100000001", balance=0)
        d_repo.init_member_dues(alice_id, 2026)
        tx_id = t_repo.create_draft(alice_id, 10000)
        t_repo.approve(tx_id, 0, 0, ["Februari"], "admin")
        d_repo.mark_paid(d_repo.get_unpaid(alice_id, 2026)[0].id, tx_id)

    # Calling execute_undo inside db.transaction() must NOT crash
    with db.transaction() as write_conn:
        res = execute_undo(write_conn, tx_id)
        assert res.success is True

    # Check that transaction was reverted
    with db.connect() as read_conn:
        tx = TransactionRepository(read_conn).get_by_id(tx_id)
        assert tx is not None
        assert tx.status == "REVERTED"


def test_execute_koreksi_inside_db_transaction_context_manager(temp_db_path):
    """Adversarial check: execute_koreksi must work seamlessly inside Database.transaction().

    Calling execute_koreksi inside db.transaction() must commit atomically without
    'cannot commit - no transaction is active' error.
    """
    db = init_db(temp_db_path)

    with db.connect() as conn:
        m_repo = MemberRepository(conn)
        d_repo = MonthlyDuesRepository(conn)
        t_repo = TransactionRepository(conn)

        alice_id = m_repo.create("Alice", "6281100000001", balance=0)
        d_repo.init_member_dues(alice_id, 2026)
        tx_id = t_repo.create_draft(alice_id, 10000)
        t_repo.approve(tx_id, 0, 0, ["Februari"], "admin")
        d_repo.mark_paid(d_repo.get_unpaid(alice_id, 2026)[0].id, tx_id)

    with db.transaction() as write_conn:
        res = execute_koreksi(write_conn, tx_id, new_amount=20000, approved_by="admin")
        assert res.success is True
        assert res.new_amount == 20000


def test_concurrent_double_koreksi_race_condition(temp_db_path):
    """Stress test: Two concurrent threads execute koreksi on the same transaction simultaneously.

    Without transaction isolation, both threads read the transaction as APPROVED,
    both undo it, and both re-allocate, leading to corrupted dues matrix and duplicate payments.
    """
    db = init_db(temp_db_path)

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

    def koreksi_worker(worker_id: int, amount: int):
        conn = sqlite3.connect(temp_db_path, timeout=30.0, autocommit=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")

        barrier.wait()
        try:
            res = execute_koreksi(conn, tx_id, new_amount=amount, approved_by=f"worker_{worker_id}")
            results.append({"worker": worker_id, "success": True, "res": res})
        except Exception as e:
            results.append({"worker": worker_id, "success": False, "error": str(e)})
        finally:
            conn.close()

    t1 = threading.Thread(target=koreksi_worker, args=(1, 20000))
    t2 = threading.Thread(target=koreksi_worker, args=(2, 30000))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    # Exactly one thread must succeed and the other must fail with invalid state or conflict
    assert len(successes) == 1, (
        f"Race condition in koreksi! Both succeeded: {results}. "
        "Concurrent koreksi must serialize so only one succeeds."
    )
    assert len(failures) == 1
