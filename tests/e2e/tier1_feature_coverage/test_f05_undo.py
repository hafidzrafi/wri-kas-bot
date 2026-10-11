"""Tier 1: Feature Coverage: Feature 5: Atomic Undo & Koreksi Engine (undo.py)."""

import sqlite3

from wri_kas_bot.undo import execute_undo


def test_execute_undo_reverts_balance(initialized_db):
    """Verify executing undo restores member balance to balance_before."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sheets_cells_filled) "
        "VALUES (1, 15000, 'TRANSFER_JAGO', 0, 5000, '[[2026, 2]]', 'APPROVED', "
        "'[\"PENGURUS-2025/2026!D5\"]')"
    )
    tx_id = cursor.lastrowid
    cursor.execute("UPDATE members SET balance = 5000 WHERE id = 1")
    cursor.execute(
        "UPDATE monthly_dues SET status = 'PAID', transaction_id = ? "
        "WHERE member_id = 1 AND month_index = 2",
        (tx_id,),
    )
    conn.commit()

    result = execute_undo(conn, tx_id)

    cursor.execute("SELECT balance FROM members WHERE id = 1")
    current_balance = cursor.fetchone()[0]
    conn.close()

    assert result.transaction_id == tx_id
    assert result.restored_balance == 0
    assert current_balance == 0


def test_execute_undo_reopens_months(initialized_db):
    """Verify executing undo resets monthly_dues back to UNPAID."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sheets_cells_filled) "
        "VALUES (1, 20000, 'TRANSFER_JAGO', 0, 0, '[[2026, 2], [2026, 3]]', 'APPROVED', "
        '\'["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!E5"]\')'
    )
    tx_id = cursor.lastrowid
    cursor.execute(
        "UPDATE monthly_dues SET status = 'PAID', transaction_id = ? "
        "WHERE member_id = 1 AND month_index IN (2, 3)",
        (tx_id,),
    )
    conn.commit()

    result = execute_undo(conn, tx_id)

    cursor.execute(
        "SELECT month_index, status FROM monthly_dues WHERE member_id = 1 AND month_index IN (2, 3)"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(result.reverted_months) == 2
    for r in rows:
        assert r["status"] == "UNPAID"


def test_execute_undo_extracts_sheets_cells(initialized_db):
    """Verify UndoResult includes exact cell list to batch clear."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status, sheets_cells_filled) "
        "VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, '[[2026, 2]]', 'APPROVED', "
        "'[\"PENGURUS-2025/2026!D5\"]')"
    )
    tx_id = cursor.lastrowid
    conn.commit()

    result = execute_undo(conn, tx_id)
    conn.close()

    assert result.sheets_cells_to_clear == ["PENGURUS-2025/2026!D5"]


def test_execute_undo_updates_transaction_status(initialized_db):
    """Verify transaction status is set to REVERTED."""
    conn = sqlite3.connect(initialized_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transactions "
        "(member_id, amount_paid, payment_method, balance_before, balance_after, "
        "months_filled, status) "
        "VALUES (1, 10000, 'TRANSFER_JAGO', 0, 0, '[[2026, 2]]', 'APPROVED')"
    )
    tx_id = cursor.lastrowid
    conn.commit()

    execute_undo(conn, tx_id)

    cursor.execute("SELECT status FROM transactions WHERE id = ?", (tx_id,))
    status = cursor.fetchone()[0]
    conn.close()

    assert status == "REVERTED"


def test_execute_undo_zero_month_deposit_reversal(initialized_db):
    """Verify undo of a partial deposit payment with 0 cleared months."""
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

    result = execute_undo(conn, tx_id)

    cursor.execute("SELECT balance FROM members WHERE id = 1")
    balance = cursor.fetchone()[0]
    conn.close()

    assert result.restored_balance == 0
    assert result.sheets_cells_to_clear == []
    assert balance == 0
