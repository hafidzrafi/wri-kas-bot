"""Atomic Undo & Koreksi Rollback Engine for wri-kas-bot.

Guarantees atomic rollback and correction of approved dues transactions, restoring
deposit balances, reopening monthly dues, and generating cell ranges for Google Sheets batchClear.
Enforces the Chronological Latest Transaction Guard.
"""

import contextlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any

from wri_kas_bot.fifo import allocate_dues

logger = logging.getLogger(__name__)

_active_tx_owners: dict[int, int] = {}
_tx_lock_mutex = threading.Lock()


class UndoError(Exception):
    """Base exception for undo and rollback engine failures."""

    pass


class TransactionNotFoundError(UndoError):
    """Raised when the target transaction does not exist."""

    pass


class InvalidTransactionStateError(UndoError):
    """Raised when target transaction is not in APPROVED state."""

    pass


class SupersededTransactionError(UndoError):
    """Raised when a newer approved transaction exists for the same member."""

    pass


@dataclass(frozen=True)
class UndoResult:
    """Result of transaction rollback adhering to PROJECT.md §4."""

    transaction_id: int
    member_id: int | None = None
    reverted_months: list[tuple[int, int]] = field(default_factory=list)
    restored_balance: int | None = None
    sheets_cells_to_clear: list[str] = field(default_factory=list)
    success: bool = True
    message: str = ""


@dataclass(frozen=True)
class CorrectionResult(UndoResult):
    """Result of transaction /koreksi re-allocation."""

    new_amount: int = 0
    new_months_filled: list[tuple[int, int]] = field(default_factory=list)
    new_balance: int = 0


def _commit(conn: sqlite3.Connection) -> None:
    """Commit connection transaction safely under both autocommit and legacy modes."""
    if getattr(conn, "autocommit", False) is True:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("COMMIT;")
    else:
        conn.commit()


def _extract_sheets_cells(raw: Any) -> list[str]:
    """Parse JSON or structured list into standard Google Sheets cell coordinates."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return [raw] if "!" in raw else []
    else:
        parsed = raw

    cells: list[str] = []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and "cell" in item:
                sheet = item.get("sheet", "PENGURUS-2025/2026")
                cells.append(f"'{sheet}'!{item['cell']}")
            elif isinstance(item, str):
                cells.append(item)
    return cells


def _row_to_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any]:
    """Convert a row to dict supporting both sqlite3.Row and plain tuples."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if cursor.description:
        cols = [c[0] for c in cursor.description]
        return dict(zip(cols, row, strict=False))
    return {}


def _fetch_all_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """Fetch all rows from cursor as a list of dicts."""
    rows = cursor.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    if isinstance(rows[0], sqlite3.Row):
        return [dict(r) for r in rows]
    if cursor.description:
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, r, strict=False)) for r in rows]
    return []


def execute_undo(
    conn: sqlite3.Connection,
    transaction_id: int,
    raise_on_error: bool = True,
) -> UndoResult:
    """Atomically rollback an approved transaction.

    Rules:
    1. Transaction must exist and have status 'APPROVED'.
    2. Transaction must be the latest approved transaction for that member.
    3. Dues records linked to the transaction are reset to 'UNPAID'.
    4. Member balance is restored to balance_before.
    5. Transaction status is updated to 'REVERTED' and sync_status to 'PENDING'.
    6. Cell coordinates are extracted for Google Sheets batchClear.

    Args:
        conn: SQLite connection.
        transaction_id: Target transaction ID to roll back.
        raise_on_error: Whether to raise typed exceptions on invalid state.

    Returns:
        UndoResult containing restored balance, reverted months, and clear targets.

    Raises:
        TransactionNotFoundError: If transaction_id does not exist.
        InvalidTransactionStateError: If transaction status is not APPROVED.
        SupersededTransactionError: If a newer approved transaction exists for member.
    """
    ident = threading.get_ident()
    with _tx_lock_mutex:
        if transaction_id in _active_tx_owners and _active_tx_owners[transaction_id] != ident:
            msg = f"Transaction #{transaction_id} is currently locked by a concurrent operation."
            if raise_on_error:
                raise InvalidTransactionStateError(msg)
            return UndoResult(success=False, message=msg, transaction_id=transaction_id)
        already_owned = transaction_id in _active_tx_owners
        if not already_owned:
            _active_tx_owners[transaction_id] = ident

    originated = False
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE;")
        originated = True

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,))
        tx_row = cursor.fetchone()

        if not tx_row:
            msg = f"Transaction #{transaction_id} not found."
            if originated and conn.in_transaction:
                conn.execute("ROLLBACK;")
            if raise_on_error:
                raise TransactionNotFoundError(msg)
            return UndoResult(
                success=False,
                message=msg,
                transaction_id=transaction_id,
            )

        tx = _row_to_dict(cursor, tx_row)
        if tx["status"] != "APPROVED":
            msg = (
                f"Transaction #{transaction_id} is in status '{tx['status']}'. "
                "Only APPROVED transactions can be undone."
            )
            if originated and conn.in_transaction:
                conn.execute("ROLLBACK;")
            if raise_on_error:
                raise InvalidTransactionStateError(msg)
            return UndoResult(
                success=False,
                message=msg,
                transaction_id=transaction_id,
                member_id=tx["member_id"],
            )

        member_id = int(tx["member_id"])

        # Invariant: Must be latest approved transaction for member
        cursor.execute(
            """
            SELECT COUNT(*) as newer_count FROM transactions
            WHERE member_id = ? AND status = 'APPROVED' AND id > ?
            """,
            (member_id, transaction_id),
        )
        row = cursor.fetchone()
        newer_count = int(row[0]) if row else 0

        if newer_count > 0:
            msg = (
                f"Cannot undo transaction #{transaction_id}: a subsequent approved transaction "
                f"exists for member #{member_id}. Revert newer transactions first or use /setsaldo."
            )
            if originated and conn.in_transaction:
                conn.execute("ROLLBACK;")
            if raise_on_error:
                raise SupersededTransactionError(msg)
            return UndoResult(
                success=False,
                message=msg,
                transaction_id=transaction_id,
                member_id=member_id,
            )

        # 1. Fetch dues linked to this transaction
        cursor.execute(
            """
            SELECT year, month_index FROM monthly_dues
            WHERE transaction_id = ?
            ORDER BY month_index ASC
            """,
            (transaction_id,),
        )
        reverted_rows = _fetch_all_dicts(cursor)
        reverted_months: list[tuple[int, int]] = [
            (int(r["year"]), int(r["month_index"])) for r in reverted_rows
        ]

        # Fallback to parsing months_filled if monthly_dues was not set with transaction_id
        if not reverted_months and tx.get("months_filled"):
            try:
                raw_mf = (
                    json.loads(tx["months_filled"])
                    if isinstance(tx["months_filled"], str)
                    else tx["months_filled"]
                )
                for item in raw_mf:
                    if isinstance(item, list | tuple) and len(item) == 2:
                        reverted_months.append((int(item[0]), int(item[1])))
                    elif isinstance(item, str):
                        from wri_kas_bot.fifo import MONTH_INDEX_TO_NAME

                        name_to_idx = {v.lower(): k for k, v in MONTH_INDEX_TO_NAME.items()}
                        if item.lower() in name_to_idx:
                            reverted_months.append((2026, name_to_idx[item.lower()]))
            except Exception:
                pass

        # 2. Reset monthly dues
        cursor.execute(
            """
            UPDATE monthly_dues
            SET status = 'UNPAID', transaction_id = NULL, payment_method = NULL,
                proof_url = NULL, paid_at = NULL
            WHERE transaction_id = ?
            """,
            (transaction_id,),
        )

        # 3. Restore member balance to balance_before
        balance_before = int(tx["balance_before"])
        cursor.execute(
            "UPDATE members SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (balance_before, member_id),
        )

        # 4. Extract sheets cells to clear
        sheets_cells_to_clear = _extract_sheets_cells(tx.get("sheets_cells_filled"))

        # 5. Mark transaction REVERTED and sync_status PENDING
        cursor.execute(
            """
            UPDATE transactions
            SET status = 'REVERTED', sync_status = 'PENDING', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (transaction_id,),
        )

        if originated and conn.in_transaction:
            conn.execute("COMMIT;")

        return UndoResult(
            transaction_id=transaction_id,
            member_id=member_id,
            reverted_months=reverted_months,
            restored_balance=balance_before,
            sheets_cells_to_clear=sheets_cells_to_clear,
            success=True,
            message=f"Transaction #{transaction_id} successfully reverted.",
        )
    except Exception:
        if originated and conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        if not already_owned:
            with _tx_lock_mutex:
                if _active_tx_owners.get(transaction_id) == ident:
                    del _active_tx_owners[transaction_id]


def execute_koreksi(
    conn: sqlite3.Connection,
    transaction_id: int,
    new_amount: int,
    approved_by: str,
    due_per_month: int = 10000,
    raise_on_error: bool = True,
) -> CorrectionResult:
    """Atomically roll back old allocation and re-allocate with new_amount.

    Args:
        conn: SQLite connection.
        transaction_id: Approved transaction ID to correct.
        new_amount: New payment amount in IDR.
        approved_by: Identifier of treasurer approving correction.
        due_per_month: Fee per monthly due (default 10000 IDR).
        raise_on_error: Whether to raise typed exceptions on invalid state.

    Returns:
        CorrectionResult containing updated balance and filled months.
    """
    if new_amount < 0:
        raise ValueError("new_amount cannot be negative")

    ident = threading.get_ident()
    with _tx_lock_mutex:
        if transaction_id in _active_tx_owners and _active_tx_owners[transaction_id] != ident:
            msg = f"Transaction #{transaction_id} is currently locked by a concurrent operation."
            if raise_on_error:
                raise InvalidTransactionStateError(msg)
            return CorrectionResult(transaction_id=transaction_id, success=False, message=msg)
        already_owned = transaction_id in _active_tx_owners
        if not already_owned:
            _active_tx_owners[transaction_id] = ident

    originated = False
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE;")
        originated = True

    try:
        # Step 1: Execute undo to rollback prior state
        undo_res = execute_undo(conn, transaction_id, raise_on_error=raise_on_error)
        if not undo_res.success:
            if originated and conn.in_transaction:
                conn.execute("ROLLBACK;")
            return CorrectionResult(
                transaction_id=transaction_id,
                member_id=undo_res.member_id,
                success=False,
                message=undo_res.message,
            )

        member_id = int(undo_res.member_id)  # type: ignore[arg-type]
        restored_balance = int(undo_res.restored_balance or 0)

        cursor = conn.cursor()
        # Step 2: Query all unpaid dues chronologically
        cursor.execute(
            """
            SELECT year, month_index, month FROM monthly_dues
            WHERE member_id = ? AND status = 'UNPAID'
            ORDER BY year ASC, month_index ASC
            """,
            (member_id,),
        )
        unpaid = _fetch_all_dicts(cursor)

        # Step 3: Re-run FIFO allocation starting from restored balance
        alloc = allocate_dues(
            unpaid_months=unpaid,
            amount_paid=new_amount,
            current_balance=restored_balance,
            due_per_month=due_per_month,
        )

        # Step 4: Update member balance
        cursor.execute(
            "UPDATE members SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (alloc.balance_after, member_id),
        )

        # Step 5: Mark new months as PAID linked to transaction_id
        cursor.execute(
            "SELECT payment_method, proof_drive_url FROM transactions WHERE id = ?",
            (transaction_id,),
        )
        tx_meta = _row_to_dict(cursor, cursor.fetchone())
        payment_method = str(tx_meta.get("payment_method", "TRANSFER_JAGO"))
        proof_url = tx_meta.get("proof_drive_url") or ("Tunai" if payment_method == "CASH" else "")

        for y, m_idx in alloc.months_filled:
            cursor.execute(
                """
                UPDATE monthly_dues
                SET status = 'PAID', transaction_id = ?, payment_method = ?,
                    proof_url = ?, paid_at = CURRENT_TIMESTAMP
                WHERE member_id = ? AND year = ? AND month_index = ?
                """,
                (transaction_id, payment_method, proof_url, member_id, y, m_idx),
            )

        # Step 6: Update transaction back to APPROVED with new amounts
        cursor.execute(
            """
            UPDATE transactions SET
                status = 'APPROVED',
                amount_paid = ?,
                balance_after = ?,
                months_filled = ?,
                approved_by = ?,
                sync_status = 'PENDING',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                new_amount,
                alloc.balance_after,
                json.dumps(alloc.months_filled),
                approved_by,
                transaction_id,
            ),
        )

        if originated and conn.in_transaction:
            conn.execute("COMMIT;")

        return CorrectionResult(
            transaction_id=transaction_id,
            member_id=member_id,
            new_amount=new_amount,
            new_months_filled=alloc.months_filled,
            new_balance=alloc.balance_after,
            reverted_months=undo_res.reverted_months,
            sheets_cells_to_clear=undo_res.sheets_cells_to_clear,
            success=True,
            message=f"Transaction #{transaction_id} successfully corrected to Rp {new_amount:,}.",
        )
    except Exception:
        if originated and conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        if not already_owned:
            with _tx_lock_mutex:
                if _active_tx_owners.get(transaction_id) == ident:
                    del _active_tx_owners[transaction_id]


execute_correction = execute_koreksi
