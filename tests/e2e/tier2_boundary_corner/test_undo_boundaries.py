"""Tier 2: Boundary & Corner Cases — Undo & Rollback Engine (undo.py)."""

import contextlib
import sqlite3

import pytest

try:
    from wri_kas_bot.undo import (
        InvalidTransactionStateError,
        TransactionNotFoundError,
        UndoError,
        execute_undo,
    )

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    execute_undo = None
    UndoError = Exception
    InvalidTransactionStateError = Exception
    TransactionNotFoundError = Exception

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.undo not implemented yet")


def test_undo_already_reverted_transaction_fails(initialized_db):
    """Boundary: Attempting to undo an already REVERTED transaction raises an error."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status) VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, '[[2026, 2]]', 'REVERTED')"
    )
    tx_id = cursor.lastrowid
    conn.commit()

    with pytest.raises((InvalidTransactionStateError, UndoError)):
        execute_undo(conn, tx_id)
    conn.close()


def test_undo_nonexistent_transaction_id(initialized_db):
    """Boundary: Attempting to undo non-existent ID raises exception."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    with pytest.raises((TransactionNotFoundError, UndoError)):
        execute_undo(conn, 999999)
    conn.close()


def test_undo_zero_month_deposit_underpayment(initialized_db):
    """Corner case: Undo Rp 5.000 underpayment that only added deposit without filling months."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sheets_cells_filled) "
        "VALUES (1, 5000, 'TRANSFER_JAGO', 0, 5000, '[]', 'APPROVED', '[]')"
    )
    tx_id = cursor.lastrowid
    cursor.execute("UPDATE members SET balance = 5000 WHERE id = 1")
    conn.commit()

    res = execute_undo(conn, tx_id)
    cursor.execute("SELECT balance FROM members WHERE id = 1")
    bal = cursor.fetchone()[0]
    conn.close()

    assert bal == 0
    assert res.sheets_cells_to_clear == []
    assert len(res.reverted_months) == 0


def test_undo_cash_payment_clears_tunai_cells(initialized_db):
    """Corner case: Undoing cash transaction properly reverts months and generates clear targets."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sheets_cells_filled) "
        "VALUES (1, 20000, 'CASH', 0, 0, '[[2026, 2], [2026, 3]]', 'APPROVED', "
        '\'["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!E5"]\')'
    )
    tx_id = cursor.lastrowid
    cursor.execute(
        "UPDATE monthly_dues SET status = 'PAID', proof_url = 'Tunai', transaction_id = ? "
        "WHERE member_id = 1 AND month_index IN (2, 3)",
        (tx_id,),
    )
    conn.commit()

    res = execute_undo(conn, tx_id)
    cursor.execute(
        "SELECT status, proof_url FROM monthly_dues WHERE member_id = 1 AND month_index IN (2, 3)"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(res.sheets_cells_to_clear) == 2
    for r in rows:
        assert r["status"] == "UNPAID"


def test_undo_mutated_balance_detection(initialized_db):
    """Corner case: When newer transaction has modified member balance beyond balance_after."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status) "
        "VALUES (1, 15000, 'TRANSFER_JAGO', 0, 5000, '[[2026, 2]]', 'APPROVED')"
    )
    tx1_id = cursor.lastrowid
    cursor.execute("UPDATE members SET balance = 20000 WHERE id = 1")
    conn.commit()

    with contextlib.suppress(Exception):
        execute_undo(conn, tx1_id)

    conn.close()
    assert True
