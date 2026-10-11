"""Database connection and lifecycle management for Kas WRI Bot.

Provides SQLite WAL configuration, foreign key enforcement, busy timeout handling,
and atomic transaction context managers with BEGIN IMMEDIATE.
"""

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- 1. Members Directory
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'Aktif',
    balance INTEGER NOT NULL DEFAULT 0,
    last_billed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Monthly Dues Matrix (Matriks Kas Pengurus)
CREATE TABLE IF NOT EXISTS monthly_dues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    year INTEGER NOT NULL DEFAULT 2026,
    month TEXT NOT NULL,
    month_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNPAID',
    payment_method TEXT,
    proof_url TEXT,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    paid_at TIMESTAMP,
    UNIQUE(member_id, year, month)
);

-- 3. Transactions (Audit Trail & Outbox Anchor)
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    amount_paid INTEGER NOT NULL,
    payment_method TEXT NOT NULL,
    balance_before INTEGER NOT NULL DEFAULT 0,
    balance_after INTEGER NOT NULL DEFAULT 0,
    months_filled TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    proof_drive_url TEXT,
    local_image_path TEXT,
    ocr_amount INTEGER,
    ocr_bank TEXT,
    rejection_reason TEXT,
    approved_by TEXT,
    wa_message_id TEXT,
    notification_msg_id TEXT,
    sheets_cells_filled TEXT,
    sync_status TEXT NOT NULL DEFAULT 'PENDING',
    sync_error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Monthly Ledger Summaries (Debit_Kredit Aggregation)
CREATE TABLE IF NOT EXISTS monthly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_month TEXT NOT NULL,
    non_cash_amount INTEGER NOT NULL DEFAULT 0,
    cash_amount INTEGER NOT NULL DEFAULT 0,
    prev_balance INTEGER,
    new_balance INTEGER,
    target_row INTEGER,
    sync_status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Outbox Tasks (Explicit Decoupled Outbox Queue)
CREATE TABLE IF NOT EXISTS outbox_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Performance & Query Indices
CREATE INDEX IF NOT EXISTS idx_members_phone ON members(phone_number);
CREATE INDEX IF NOT EXISTS idx_members_status ON members(status);
CREATE INDEX IF NOT EXISTS idx_monthly_dues_member ON monthly_dues(member_id, year, month_index);
CREATE INDEX IF NOT EXISTS idx_monthly_dues_tx ON monthly_dues(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_sync ON transactions(sync_status);
CREATE INDEX IF NOT EXISTS idx_transactions_notif_msg ON transactions(notification_msg_id);
CREATE INDEX IF NOT EXISTS idx_transactions_member_created
    ON transactions(member_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbox_tasks_status ON outbox_tasks(status);
"""


class Database:
    """Manages SQLite connections, initialization, and transaction scoping."""

    def __init__(self, db_path: str = "data/kas_wri.db", timeout: float = 30.0) -> None:
        self.db_path = db_path
        self.timeout = timeout
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        if self.db_path != ":memory:":
            db_dir = Path(self.db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Provides a scoped read/general SQLite connection.

        Enforces foreign keys, busy timeout, WAL mode, and Row factory.
        Unconditionally closes the connection upon exiting the context.
        """
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.timeout,
            autocommit=True,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)};")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Provides an atomic write transaction context manager.

        Executes BEGIN IMMEDIATE to prevent lock upgrade deadlocks.
        Executes COMMIT on successful completion, and ROLLBACK if an error occurs.
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                if conn.in_transaction:
                    conn.execute("COMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK;")
                raise

    def init_schema(self) -> None:
        """Executes full DDL script to create tables and indices idempotently."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info("Initialized SQLite database schema at %s", self.db_path)


def init_db(db_path: str = "data/kas_wri.db") -> Database:
    """Factory helper to instantiate and initialize the database schema."""
    db = Database(db_path)
    db.init_schema()
    return db
