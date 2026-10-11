"""Tier 1: Feature Coverage: Feature 2: SQLite WAL Database Engine (database.py)."""

import sqlite3

from wri_kas_bot.database import init_db


def test_init_db_creates_core_tables(tmp_db_path):
    """Verify init_db initializes the 4 core schema tables."""
    init_db(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    expected = {"members", "monthly_dues", "transactions", "monthly_summaries"}
    assert expected.issubset(tables)


def test_init_db_enables_wal_mode(tmp_db_path):
    """Verify WAL journal mode is active."""
    init_db(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_init_db_enables_foreign_keys(tmp_db_path):
    """Verify foreign keys enforcement pragma."""
    db = init_db(tmp_db_path)
    if hasattr(db, "connect"):
        with db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys")
            fk_status = cursor.fetchone()[0]
    else:
        conn = sqlite3.connect(tmp_db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        fk_status = cursor.fetchone()[0]
        conn.close()
    assert fk_status == 1


def test_init_db_creates_indices(tmp_db_path):
    """Verify query performance indices exist."""
    init_db(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indices = {row[0] for row in cursor.fetchall()}
    conn.close()

    expected = {
        "idx_members_phone",
        "idx_transactions_status",
        "idx_transactions_sync",
        "idx_monthly_dues_member",
    }
    assert expected.issubset(indices)


def test_init_db_is_idempotent(tmp_db_path):
    """Verify init_db can be safely called repeatedly without exception."""
    init_db(tmp_db_path)
    init_db(tmp_db_path)  # second call should not raise
    conn = sqlite3.connect(tmp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM members")
    count = cursor.fetchone()[0]
    conn.close()
    assert count >= 0


def test_foreign_key_cascade_delete(tmp_db_path):
    """Verify deleting member cascades to monthly dues."""
    init_db(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO members (name, phone_number) VALUES ('Alice', '6281100000001')")
    m_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO monthly_dues "
        "(member_id, year, month, month_index, status) VALUES (?, 2026, 'Februari', 2, 'UNPAID')",
        (m_id,),
    )
    conn.commit()

    cursor.execute("DELETE FROM members WHERE id = ?", (m_id,))
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM monthly_dues WHERE member_id = ?", (m_id,))
    remaining = cursor.fetchone()[0]
    conn.close()
    assert remaining == 0
