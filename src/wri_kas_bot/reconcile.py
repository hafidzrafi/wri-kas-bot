"""Standalone CLI tool to reconcile stranded transactions from prototype backlog.

Features --dry-run for non-mutating preview and --apply for committing
FIFO allocations, Google Drive uploads, and Google Sheets synchronization.
"""

import argparse
import logging
import sys
from typing import Any

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.drive import GoogleDriveClient
from wri_kas_bot.fifo import allocate_dues
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository, TransactionRepository
from wri_kas_bot.sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)


def find_stranded_transactions(db: Database) -> list[dict[str, Any]]:
    """Query transactions in PENDING status requiring reconciliation."""
    with db.connect() as conn:
        cursor = conn.execute(
            "SELECT t.*, m.name as member_name, m.phone_number, m.status as member_status "
            "FROM transactions t "
            "JOIN members m ON t.member_id = m.id "
            "WHERE t.status = 'PENDING' "
            "ORDER BY t.id ASC"
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def reconcile_backlog(
    db: Database,
    sheets_client: GoogleSheetsClient | None = None,
    drive_client: GoogleDriveClient | None = None,
    dry_run: bool = True,
) -> int:
    """Run reconciliation on stranded backlog transactions.

    Returns the count of processed transactions.
    """
    settings = get_settings()
    sheets = sheets_client or GoogleSheetsClient()
    candidates = find_stranded_transactions(db)

    if not candidates:
        logger.info("No stranded transactions found in backlog.")
        return 0

    logger.info("Found %d stranded transactions in backlog:", len(candidates))
    for c in candidates:
        name_phone = f"{c['member_name']} ({c['phone_number']})"
        logger.info(
            " - #%s | Member: %s | Nominal: Rp %s",
            c["id"],
            name_phone,
            f"{c['amount_paid']:,}",
        )

    if dry_run:
        logger.info(
            "[DRY RUN] Preview mode active. No changes committed to database or Google Sheets."
        )
        return len(candidates)

    logger.info("[APPLY] Committing FIFO allocations and syncing with Google Sheets...")
    processed_count = 0
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        member_repo = MemberRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)

        for c in candidates:
            tx_id = c["id"]
            member_id = c["member_id"]
            nominal = c["amount_paid"] or c["ocr_amount"] or settings.due_amount_per_month
            member = member_repo.get_by_id(member_id)
            if not member:
                continue

            unpaid_dues = dues_repo.get_unpaid(member_id, year=2026)
            alloc_result = allocate_dues(
                unpaid_months=unpaid_dues,
                amount_paid=nominal,
                current_balance=member["balance"],
                due_per_month=settings.due_amount_per_month,
            )

            unpaid_map = {(d.year, d.month_index): d for d in unpaid_dues}
            for y, m_idx in alloc_result.months_filled:
                due_item = unpaid_map.get((y, m_idx))
                if due_item:
                    dues_repo.mark_paid(
                        due_id=due_item.id,
                        transaction_id=tx_id,
                        payment_method=c["payment_method"],
                        proof_url=c["proof_drive_url"] or "",
                    )

            months_list = alloc_result.cleared_month_names
            member_repo.update_balance(member_id, alloc_result.balance_after)
            tx_repo.approve(
                tx_id=tx_id,
                balance_before=alloc_result.balance_before,
                balance_after=alloc_result.balance_after,
                months_filled=months_list,
                approved_by="CLI_RECONCILER",
            )

            # Sync to Google Sheets
            is_cash = c["payment_method"] == "CASH"
            dues_cell_val = "Tunai" if is_cash else "Bank Jago"
            bukti_url_val = "Tunai" if is_cash else (c["proof_drive_url"] or "")
            recorded_coords = []
            for m_name in months_list:
                coord = sheets.update_payment_month(c["member_name"], m_name, dues_cell_val)
                recorded_coords.append(coord)

            sheets.update_member_balance(c["phone_number"], alloc_result.balance_after)

            months_str = ", ".join(months_list) if months_list else "Masuk saldo"
            log_entry = {
                "Id": tx_id,
                "Timestamp": c["created_at"],
                "Nama": c["member_name"],
                "Nomor HP": c["phone_number"],
                "Nominal": nominal,
                "Bulan Terisi": months_str,
                "Sisa Saldo": alloc_result.balance_after,
                "Status": "APPROVED",
                "Bukti URL": bukti_url_val,
                "Approved By": "CLI_RECONCILER",
                "Keterangan": "Reconciled from backlog",
            }
            sheets.append_log_transaksi(log_entry)

            conn.execute(
                "UPDATE transactions SET sync_status = 'SYNCED', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (tx_id,),
            )
            processed_count += 1

    logger.info("Successfully reconciled %d transactions.", processed_count)
    return processed_count


def main(argv: list[str] | None = None) -> int:
    """CLI tool entrypoint."""
    parser = argparse.ArgumentParser(description="WRI Kas Bot Backlog Reconciler")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview backlog mutations without applying changes (default)",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Commit transactions and execute Google Sheets updates",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database",
    )

    args = parser.parse_args(argv)
    settings = get_settings()
    db_path = args.db_path or settings.database_url
    if "sqlite" in db_path:
        db_path = db_path.split(":///")[-1]

    db = Database(db_path)
    is_apply = getattr(args, "apply", False)
    is_dry_run = not is_apply

    reconcile_backlog(db=db, dry_run=is_dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
