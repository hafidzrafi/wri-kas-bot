"""Domain repositories for Member, MonthlyDue, Transaction, Outbox, and Summary.

Encapsulates SQL queries with parameter binding, schema integrity, and dataclass models.
"""

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

MONTHS: list[tuple[int, str]] = [
    (2, "Februari"),
    (3, "Maret"),
    (4, "April"),
    (5, "Mei"),
    (6, "Juni"),
    (7, "Juli"),
    (8, "Agustus"),
    (9, "September"),
    (10, "Oktober"),
    (11, "November"),
    (12, "Desember"),
]

MONTHS_SCHEDULE = MONTHS


class DictAttrMixin:
    """Provides dictionary-like subscripting and .get() access to dataclasses."""

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as err:
            raise KeyError(key) from err

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def keys(self):
        return asdict(self).keys()

    def values(self):
        return asdict(self).values()

    def items(self):
        return asdict(self).items()


@dataclass(frozen=True)
class Member(DictAttrMixin):
    id: int
    name: str
    phone_number: str
    status: str = "Aktif"
    balance: int = 0
    last_billed_at: datetime | str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class MonthlyDue(DictAttrMixin):
    id: int
    member_id: int
    year: int
    month: str
    month_index: int
    status: str = "UNPAID"
    payment_method: str | None = None
    proof_url: str | None = None
    transaction_id: int | None = None
    paid_at: str | None = None


@dataclass(frozen=True)
class Transaction(DictAttrMixin):
    id: int
    member_id: int
    amount_paid: int
    payment_method: str
    balance_before: int = 0
    balance_after: int = 0
    months_filled: list[Any] = field(default_factory=list)
    status: str = "PENDING"
    proof_drive_url: str | None = None
    local_image_path: str | None = None
    ocr_amount: int | None = None
    ocr_bank: str | None = None
    rejection_reason: str | None = None
    approved_by: str | None = None
    wa_message_id: str | None = None
    notification_msg_id: str | None = None
    sheets_cells_filled: list[Any] = field(default_factory=list)
    sync_status: str = "PENDING"
    sync_error: str | None = None
    retry_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class OutboxTask(DictAttrMixin):
    id: int
    transaction_id: int | None
    task_type: str
    payload: dict[str, Any]
    status: str = "PENDING"
    retry_count: int = 0
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class MonthlySummary(DictAttrMixin):
    id: int
    period_month: str
    non_cash_amount: int = 0
    cash_amount: int = 0
    prev_balance: int | None = None
    new_balance: int | None = None
    target_row: int | None = None
    sync_status: str = "PENDING"
    created_at: str | None = None


def _row_to_member(row: sqlite3.Row | dict[str, Any] | None) -> Member | None:
    if not row:
        return None
    d = dict(row)
    return Member(
        id=int(d["id"]),
        name=str(d["name"]),
        phone_number=str(d["phone_number"]),
        status=str(d.get("status", "Aktif")),
        balance=int(d.get("balance", 0)),
        last_billed_at=d.get("last_billed_at"),
        created_at=str(d.get("created_at")) if d.get("created_at") else None,
        updated_at=str(d.get("updated_at")) if d.get("updated_at") else None,
    )


def _row_to_due(row: sqlite3.Row | dict[str, Any] | None) -> MonthlyDue | None:
    if not row:
        return None
    d = dict(row)
    return MonthlyDue(
        id=int(d["id"]),
        member_id=int(d["member_id"]),
        year=int(d["year"]),
        month=str(d["month"]),
        month_index=int(d["month_index"]),
        status=str(d.get("status", "UNPAID")),
        payment_method=d.get("payment_method"),
        proof_url=d.get("proof_url"),
        transaction_id=int(d["transaction_id"]) if d.get("transaction_id") is not None else None,
        paid_at=str(d.get("paid_at")) if d.get("paid_at") else None,
    )


def _row_to_tx(row: sqlite3.Row | dict[str, Any] | None) -> Transaction | None:
    if not row:
        return None
    d = dict(row)

    months_raw = d.get("months_filled")
    months_filled: list[Any] = []
    if months_raw:
        try:
            months_filled = (
                json.loads(months_raw) if isinstance(months_raw, str) else list(months_raw)
            )
        except Exception:
            months_filled = []

    cells_raw = d.get("sheets_cells_filled")
    sheets_cells_filled: list[Any] = []
    if cells_raw:
        try:
            sheets_cells_filled = (
                json.loads(cells_raw) if isinstance(cells_raw, str) else list(cells_raw)
            )
        except Exception:
            sheets_cells_filled = []

    return Transaction(
        id=int(d["id"]),
        member_id=int(d["member_id"]),
        amount_paid=int(d["amount_paid"]),
        payment_method=str(d["payment_method"]),
        balance_before=int(d.get("balance_before", 0)),
        balance_after=int(d.get("balance_after", 0)),
        months_filled=months_filled,
        status=str(d.get("status", "PENDING")),
        proof_drive_url=d.get("proof_drive_url"),
        local_image_path=d.get("local_image_path"),
        ocr_amount=int(d["ocr_amount"]) if d.get("ocr_amount") is not None else None,
        ocr_bank=d.get("ocr_bank"),
        rejection_reason=d.get("rejection_reason"),
        approved_by=d.get("approved_by"),
        wa_message_id=d.get("wa_message_id"),
        notification_msg_id=d.get("notification_msg_id"),
        sheets_cells_filled=sheets_cells_filled,
        sync_status=str(d.get("sync_status", "PENDING")),
        sync_error=d.get("sync_error"),
        retry_count=int(d.get("retry_count", 0)),
        created_at=str(d.get("created_at")) if d.get("created_at") else None,
        updated_at=str(d.get("updated_at")) if d.get("updated_at") else None,
    )


def _row_to_task(row: sqlite3.Row | dict[str, Any] | None) -> OutboxTask | None:
    if not row:
        return None
    d = dict(row)
    payload_raw = d.get("payload", "{}")
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    return OutboxTask(
        id=int(d["id"]),
        transaction_id=int(d["transaction_id"]) if d.get("transaction_id") is not None else None,
        task_type=str(d["task_type"]),
        payload=payload,
        status=str(d.get("status", "PENDING")),
        retry_count=int(d.get("retry_count", 0)),
        last_error=d.get("last_error"),
        created_at=str(d.get("created_at")) if d.get("created_at") else None,
        updated_at=str(d.get("updated_at")) if d.get("updated_at") else None,
    )


class MemberRepository:
    """Repository handling member directory, status, and deposit balance."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, name: str, phone_number: str, status: str = "Aktif", balance: int = 0) -> int:
        cur = self.conn.execute(
            "INSERT INTO members (name, phone_number, status, balance) VALUES (?, ?, ?, ?)",
            (name, phone_number, status, balance),
        )
        return int(cur.lastrowid)

    def get_by_phone(self, phone: str) -> Member | None:
        row = self.conn.execute("SELECT * FROM members WHERE phone_number = ?", (phone,)).fetchone()
        return _row_to_member(row)

    def find_by_phone(self, phone: str) -> Member | None:
        return self.get_by_phone(phone)

    def get_by_id(self, member_id: int) -> Member | None:
        row = self.conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        return _row_to_member(row)

    def find_by_id(self, member_id: int) -> Member | None:
        return self.get_by_id(member_id)

    def list_active(self) -> list[Member]:
        rows = self.conn.execute(
            "SELECT * FROM members WHERE status = 'Aktif' ORDER BY name ASC"
        ).fetchall()
        return [m for r in rows if (m := _row_to_member(r)) is not None]

    def update_balance(self, member_id: int, new_balance: int) -> None:
        self.conn.execute(
            "UPDATE members SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_balance, member_id),
        )

    def update_last_billed(self, member_id: int, timestamp: str | None = None) -> None:
        if timestamp:
            self.conn.execute(
                "UPDATE members SET last_billed_at = ? WHERE id = ?",
                (timestamp, member_id),
            )
        else:
            self.conn.execute(
                "UPDATE members SET last_billed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (member_id,),
            )

    def update_last_billed_at(self, member_id: int, timestamp: str | None = None) -> None:
        self.update_last_billed(member_id, timestamp)


class MonthlyDuesRepository:
    """Repository handling member dues matrix and status tracking."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def init_member_dues(self, member_id: int, year: int = 2026) -> None:
        for month_idx, month_name in MONTHS:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO monthly_dues (member_id, year, month, month_index, status)
                VALUES (?, ?, ?, ?, 'UNPAID')
                """,
                (member_id, year, month_name, month_idx),
            )

    def get_unpaid(self, member_id: int, year: int = 2026) -> list[MonthlyDue]:
        rows = self.conn.execute(
            """
            SELECT * FROM monthly_dues
            WHERE member_id = ? AND year = ? AND status = 'UNPAID'
            ORDER BY month_index ASC
            """,
            (member_id, year),
        ).fetchall()
        return [m for r in rows if (m := _row_to_due(r)) is not None]

    def get_unpaid_months(self, member_id: int, year: int = 2026) -> list[MonthlyDue]:
        return self.get_unpaid(member_id, year)

    def get_paid(self, member_id: int, year: int = 2026) -> list[MonthlyDue]:
        rows = self.conn.execute(
            """
            SELECT * FROM monthly_dues
            WHERE member_id = ? AND year = ? AND status = 'PAID'
            ORDER BY month_index ASC
            """,
            (member_id, year),
        ).fetchall()
        return [m for r in rows if (m := _row_to_due(r)) is not None]

    def mark_paid(
        self,
        due_id: int,
        transaction_id: int | None = None,
        payment_method: str = "TRANSFER_JAGO",
        proof_url: str = "",
        tx_id: int | None = None,
    ) -> None:
        actual_tx_id = transaction_id if transaction_id is not None else tx_id
        self.conn.execute(
            """
            UPDATE monthly_dues
            SET status = 'PAID', transaction_id = ?, payment_method = ?,
                proof_url = ?, paid_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (actual_tx_id, payment_method, proof_url, due_id),
        )

    def revert_to_unpaid(self, transaction_id: int) -> list[MonthlyDue]:
        rows = self.conn.execute(
            "SELECT * FROM monthly_dues WHERE transaction_id = ? ORDER BY month_index ASC",
            (transaction_id,),
        ).fetchall()
        reverted = [m for r in rows if (m := _row_to_due(r)) is not None]
        self.conn.execute(
            """
            UPDATE monthly_dues
            SET status = 'UNPAID', transaction_id = NULL, payment_method = NULL,
                proof_url = NULL, paid_at = NULL
            WHERE transaction_id = ?
            """,
            (transaction_id,),
        )
        return reverted


MonthlyDueRepository = MonthlyDuesRepository


class TransactionRepository:
    """Repository handling transaction lifecycle, receipts, and outbox synchronization."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_draft(
        self,
        member_id: int,
        amount_paid: int,
        payment_method: str = "TRANSFER_JAGO",
        local_image_path: str | None = None,
        ocr_amount: int | None = None,
        ocr_bank: str | None = None,
        wa_message_id: str | None = None,
        balance_before: int = 0,
        balance_after: int = 0,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO transactions (
                member_id, amount_paid, payment_method, local_image_path,
                ocr_amount, ocr_bank, wa_message_id, status,
                balance_before, balance_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
            """,
            (
                member_id,
                amount_paid,
                payment_method,
                local_image_path,
                ocr_amount,
                ocr_bank,
                wa_message_id,
                balance_before,
                balance_after,
            ),
        )
        return int(cur.lastrowid)

    def create_transaction(
        self,
        member_id: int,
        amount_paid: int,
        payment_method: str = "TRANSFER_JAGO",
        balance_before: int = 0,
        balance_after: int = 0,
        status: str = "PENDING",
        months_filled: list[Any] | None = None,
        proof_drive_url: str | None = None,
        local_image_path: str | None = None,
        ocr_amount: int | None = None,
        ocr_bank: str | None = None,
        wa_message_id: str | None = None,
        sheets_cells_filled: list[Any] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO transactions (
                member_id, amount_paid, payment_method, balance_before, balance_after,
                status, months_filled, proof_drive_url, local_image_path,
                ocr_amount, ocr_bank, wa_message_id, sheets_cells_filled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member_id,
                amount_paid,
                payment_method,
                balance_before,
                balance_after,
                status,
                json.dumps(months_filled or []),
                proof_drive_url,
                local_image_path,
                ocr_amount,
                ocr_bank,
                wa_message_id,
                json.dumps(sheets_cells_filled or []),
            ),
        )
        return int(cur.lastrowid)

    def get_by_id(self, tx_id: int) -> Transaction | None:
        row = self.conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        return _row_to_tx(row)

    def get_by_notification_msg_id(self, msg_id: str) -> Transaction | None:
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE notification_msg_id = ?", (msg_id,)
        ).fetchone()
        return _row_to_tx(row)

    def set_notification_msg_id(self, tx_id: int, notification_msg_id: str) -> None:
        self.conn.execute(
            "UPDATE transactions SET notification_msg_id = ? WHERE id = ?",
            (notification_msg_id, tx_id),
        )

    def approve(
        self,
        tx_id: int,
        balance_before: int,
        balance_after: int,
        months_filled: list[Any],
        approved_by: str,
        sheets_cells_filled: list[Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE transactions SET
                status = 'APPROVED',
                balance_before = ?,
                balance_after = ?,
                months_filled = ?,
                approved_by = ?,
                sheets_cells_filled = ?,
                sync_status = 'PENDING',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                balance_before,
                balance_after,
                json.dumps(months_filled),
                approved_by,
                json.dumps(sheets_cells_filled or []),
                tx_id,
            ),
        )

    def reject(self, tx_id: int, reason: str, approved_by: str) -> None:
        self.conn.execute(
            """
            UPDATE transactions SET
                status = 'REJECTED',
                rejection_reason = ?,
                approved_by = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (reason, approved_by, tx_id),
        )

    def revert(self, tx_id: int) -> None:
        self.conn.execute(
            """
            UPDATE transactions SET
                status = 'REVERTED',
                sync_status = 'PENDING',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (tx_id,),
        )

    def get_by_status(self, status: str) -> list[Transaction]:
        rows = self.conn.execute(
            "SELECT * FROM transactions WHERE status = ? ORDER BY id ASC", (status,)
        ).fetchall()
        return [t for r in rows if (t := _row_to_tx(r)) is not None]

    def get_latest_approved_for_member(self, member_id: int) -> Transaction | None:
        row = self.conn.execute(
            """
            SELECT * FROM transactions
            WHERE member_id = ? AND status = 'APPROVED'
            ORDER BY id DESC LIMIT 1
            """,
            (member_id,),
        ).fetchone()
        return _row_to_tx(row)

    def get_pending_sync(self, limit: int = 10) -> list[Transaction]:
        rows = self.conn.execute(
            """
            SELECT * FROM transactions
            WHERE sync_status = 'PENDING' AND status IN ('APPROVED', 'REVERTED')
            ORDER BY id ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [t for r in rows if (t := _row_to_tx(r)) is not None]

    def update_sync_status(
        self,
        tx_id: int,
        sync_status: str,
        error: str | None = None,
        proof_drive_url: str | None = None,
        sheets_cells: str | list[Any] | None = None,
    ) -> None:
        updates: list[str] = [
            "sync_status = ?",
            "sync_error = ?",
            "retry_count = retry_count + 1",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params: list[Any] = [sync_status, error]

        if proof_drive_url is not None:
            updates.append("proof_drive_url = ?")
            params.append(proof_drive_url)

        if sheets_cells is not None:
            updates.append("sheets_cells_filled = ?")
            cells_json = sheets_cells if isinstance(sheets_cells, str) else json.dumps(sheets_cells)
            params.append(cells_json)

        params.append(tx_id)
        sql = f"UPDATE transactions SET {', '.join(updates)} WHERE id = ?"
        self.conn.execute(sql, params)


class OutboxRepository:
    """Repository managing asynchronous background synchronization tasks."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def enqueue(self, transaction_id: int | None, task_type: str, payload: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO outbox_tasks (transaction_id, task_type, payload, status)
            VALUES (?, ?, ?, 'PENDING')
            """,
            (transaction_id, task_type, json.dumps(payload)),
        )
        return int(cur.lastrowid)

    def get_pending(self, limit: int = 10) -> list[OutboxTask]:
        rows = self.conn.execute(
            """
            SELECT * FROM outbox_tasks
            WHERE status = 'PENDING'
            ORDER BY id ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [t for r in rows if (t := _row_to_task(r)) is not None]

    def mark_completed(self, task_id: int) -> None:
        self.conn.execute(
            """
            UPDATE outbox_tasks
            SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (task_id,),
        )

    def mark_failed(self, task_id: int, error: str, max_retries: int = 5) -> None:
        self.conn.execute(
            """
            UPDATE outbox_tasks
            SET retry_count = retry_count + 1,
                last_error = ?,
                status = CASE WHEN retry_count + 1 >= ? THEN 'FAILED' ELSE 'PENDING' END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error, max_retries, task_id),
        )


class MonthlySummaryRepository:
    """Repository handling monthly organization ledger aggregation for Debit_Kredit."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        period_month: str,
        non_cash_amount: int,
        cash_amount: int,
        prev_balance: int | None = None,
        new_balance: int | None = None,
        target_row: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO monthly_summaries (
                period_month, non_cash_amount, cash_amount,
                prev_balance, new_balance, target_row, sync_status
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (period_month, non_cash_amount, cash_amount, prev_balance, new_balance, target_row),
        )
        return int(cur.lastrowid)

    def get_pending_sync(self) -> list[MonthlySummary]:
        rows = self.conn.execute(
            "SELECT * FROM monthly_summaries WHERE sync_status = 'PENDING' ORDER BY id ASC"
        ).fetchall()
        return [
            MonthlySummary(
                id=int(r["id"]),
                period_month=str(r["period_month"]),
                non_cash_amount=int(r["non_cash_amount"]),
                cash_amount=int(r["cash_amount"]),
                prev_balance=int(r["prev_balance"]) if r["prev_balance"] is not None else None,
                new_balance=int(r["new_balance"]) if r["new_balance"] is not None else None,
                target_row=int(r["target_row"]) if r["target_row"] is not None else None,
                sync_status=str(r["sync_status"]),
                created_at=str(r["created_at"]) if r["created_at"] else None,
            )
            for r in rows
        ]

    def mark_synced(self, summary_id: int) -> None:
        self.conn.execute(
            "UPDATE monthly_summaries SET sync_status = 'SYNCED' WHERE id = ?",
            (summary_id,),
        )


# Functional convenience wrappers matching module-level interface contracts
def get_member_by_phone(conn: sqlite3.Connection, phone: str) -> Member | None:
    return MemberRepository(conn).get_by_phone(phone)


def update_member_balance(conn: sqlite3.Connection, member_id: int, new_balance: int) -> None:
    MemberRepository(conn).update_balance(member_id, new_balance)


def get_unpaid_months(
    conn: sqlite3.Connection, member_id: int, year: int = 2026
) -> list[MonthlyDue]:
    return MonthlyDuesRepository(conn).get_unpaid(member_id, year)


def create_transaction(
    conn: sqlite3.Connection,
    member_id: int,
    amount_paid: int,
    payment_method: str = "TRANSFER_JAGO",
    balance_before: int = 0,
    balance_after: int = 0,
    status: str = "PENDING",
) -> int:
    return TransactionRepository(conn).create_transaction(
        member_id=member_id,
        amount_paid=amount_paid,
        payment_method=payment_method,
        balance_before=balance_before,
        balance_after=balance_after,
        status=status,
    )


def get_transactions_by_status(conn: sqlite3.Connection, status: str) -> list[Transaction]:
    return TransactionRepository(conn).get_by_status(status)


def get_transaction_by_id(conn: sqlite3.Connection, tx_id: int) -> Transaction | None:
    return TransactionRepository(conn).get_by_id(tx_id)


def update_transaction_sync_status(
    conn: sqlite3.Connection,
    tx_id: int,
    sync_status: str,
    sheets_cells: str | list[Any] | None = None,
) -> None:
    TransactionRepository(conn).update_sync_status(tx_id, sync_status, sheets_cells=sheets_cells)


def update_member_last_billed_at(
    conn: sqlite3.Connection,
    member_id: int,
    timestamp: str | None = None,
) -> None:
    MemberRepository(conn).update_last_billed(member_id, timestamp)
