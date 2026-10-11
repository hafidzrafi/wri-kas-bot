"""Unit tests for SQLite database manager (src/wri_kas_bot/database.py)."""

import sqlite3

import pytest

from wri_kas_bot.database import init_db


def test_init_db_creates_tables(tmp_path):
    """Verify init_db initializes all 5 core schema tables."""
    db_file = str(tmp_path / "test.db")
    init_db(db_file)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    conn.close()

    expected = {"members", "monthly_dues", "transactions", "monthly_summaries", "outbox_tasks"}
    assert expected.issubset(tables)


def test_init_db_wal_mode(tmp_path):
    """Verify WAL journal mode is configured in database."""
    db_file = str(tmp_path / "test.db")
    init_db(db_file)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode")
    mode = cur.fetchone()[0]
    conn.close()

    assert mode.lower() == "wal"


def test_init_db_foreign_keys(tmp_path):
    """Verify connection context manager enforces foreign keys."""
    db_file = str(tmp_path / "test.db")
    db = init_db(db_file)

    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys")
        fk = cur.fetchone()[0]
        assert fk == 1


def test_init_db_creates_indices(tmp_path):
    """Verify performance indices exist."""
    db_file = str(tmp_path / "test.db")
    init_db(db_file)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indices = {r[0] for r in cur.fetchall()}
    conn.close()

    expected = {
        "idx_members_phone",
        "idx_transactions_status",
        "idx_transactions_sync",
        "idx_monthly_dues_member",
        "idx_outbox_tasks_status",
    }
    assert expected.issubset(indices)


def test_init_db_idempotence(tmp_path):
    """Verify init_db can be called repeatedly without errors or data loss."""
    db_file = str(tmp_path / "test.db")
    db = init_db(db_file)

    with db.transaction() as conn:
        conn.execute("INSERT INTO members (name, phone_number) VALUES ('Alice', '6281100000001')")

    # Second init_schema should be idempotent
    db.init_schema()

    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM members")
        assert cur.fetchone()[0] == 1


def test_foreign_key_cascade_delete(tmp_path):
    """Verify cascade deletion of dues upon member deletion."""
    db_file = str(tmp_path / "test.db")
    db = init_db(db_file)

    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO members (name, phone_number) VALUES ('Bob', '6281100000002')"
        )
        m_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO monthly_dues (member_id, year, month, month_index, status)
            VALUES (?, 2026, 'Februari', 2, 'UNPAID')
            """,
            (m_id,),
        )

    with db.transaction() as conn:
        conn.execute("DELETE FROM members WHERE id = ?", (m_id,))

    with db.connect() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM monthly_dues WHERE member_id = ?", (m_id,))
        assert cur.fetchone()[0] == 0


def test_transaction_context_manager_commit(tmp_path):
    """Verify transaction commits on normal completion."""
    db_file = str(tmp_path / "test.db")
    db = init_db(db_file)

    with db.transaction() as conn:
        conn.execute("INSERT INTO members (name, phone_number) VALUES ('Charlie', '6281100000003')")

    with db.connect() as conn:
        cur = conn.execute("SELECT name FROM members WHERE phone_number = '6281100000003'")
        row = cur.fetchone()
        assert row is not None
        assert row["name"] == "Charlie"


def test_transaction_context_manager_rollback(tmp_path):
    """Verify transaction rolls back all mutations when an exception is raised."""
    db_file = str(tmp_path / "test.db")
    db = init_db(db_file)

    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO members (name, phone_number) VALUES ('Dave', '6281100000004')")
        raise RuntimeError("Intentional failure")

    with db.connect() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM members WHERE phone_number = '6281100000004'")
        assert cur.fetchone()[0] == 0
