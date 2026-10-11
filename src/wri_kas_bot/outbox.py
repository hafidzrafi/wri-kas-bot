"""Transactional outbox worker for Kas WRI Bot.

Decouples WhatsApp webhook latency and resilience from Google Drive
and Google Sheets network operations. Automatically retries pending tasks
with exponential backoff and synchronizes rollbacks via Sheets batchClear.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from wri_kas_bot.database import Database
from wri_kas_bot.drive import GoogleDriveClient, format_drive_filename
from wri_kas_bot.repository import MemberRepository, TransactionRepository
from wri_kas_bot.sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)

# Re-exports for public interface and test compatibility
__all__ = [
    "GoogleDriveClient",
    "GoogleSheetsClient",
    "OutboxWorker",
    "format_drive_filename",
]


class OutboxWorker:
    """Processes asynchronous synchronization tasks between SQLite and Google Workspace."""

    def __init__(
        self,
        db: Database,
        drive_client: GoogleDriveClient | None = None,
        sheets_client: GoogleSheetsClient | None = None,
    ) -> None:
        self.db = db
        self.drive_client = drive_client or GoogleDriveClient()
        self.sheets_client = sheets_client or GoogleSheetsClient()

    async def process_transaction(self, tx_id: int) -> bool:
        """Process outbox synchronization for a single transaction record."""
        with self.db.transaction() as conn:
            tx_repo = TransactionRepository(conn)
            member_repo = MemberRepository(conn)

            tx = tx_repo.get_by_id(tx_id)
            if not tx:
                return False

            member = member_repo.get_by_id(tx["member_id"])
            if not member:
                return False

            member_name = member["name"]
            member_phone = member["phone_number"]
            tx_status = tx["status"]
            payment_method = tx["payment_method"]
            amount_paid = tx["amount_paid"]
            balance_after = tx["balance_after"]
            months_filled = json.loads(tx["months_filled"] or "[]")
            proof_drive_url = tx["proof_drive_url"]
            local_image_path = tx["local_image_path"]

            try:
                # 1. Drive Archival for Non-Cash Receipts
                if payment_method != "CASH" and not proof_drive_url and local_image_path:
                    img_path = Path(local_image_path)
                    if img_path.is_file():
                        img_bytes = img_path.read_bytes()
                        filename = format_drive_filename(
                            member_phone,
                            member_name,
                            tx["created_at"] or "20261011",
                        )
                        proof_drive_url = self.drive_client.upload_file(img_bytes, filename)
                        conn.execute(
                            "UPDATE transactions SET proof_drive_url = ? WHERE id = ?",
                            (proof_drive_url, tx_id),
                        )

                proof_cell_val = (
                    "Tunai" if payment_method == "CASH" else (proof_drive_url or "Via Bot")
                )

                # 2. Approved Transaction Forward Sync
                if tx_status == "APPROVED":
                    recorded_cells: list[str] = []
                    for item in months_filled:
                        # item can be "Februari" or [2026, 2] or (year, month_name)
                        if isinstance(item, list | tuple):
                            month_name = str(item[1]) if len(item) > 1 else str(item[0])
                        else:
                            month_name = str(item)

                        coord = self.sheets_client.update_payment_month(
                            name=member_name,
                            month=month_name,
                            proof_url=proof_cell_val,
                        )
                        recorded_cells.append(coord)

                    # Update member balance in data_pengurus
                    self.sheets_client.update_member_balance(
                        phone=member_phone,
                        balance=balance_after,
                    )

                    # Append to log_transaksi audit trail
                    months_str = ", ".join(
                        str(m[1]) if isinstance(m, list | tuple) and len(m) > 1 else str(m)
                        for m in months_filled
                    )
                    log_data: dict[str, Any] = {
                        "Id": tx["id"],
                        "Timestamp": tx["created_at"],
                        "Nama": member_name,
                        "Nomor HP": member_phone,
                        "Nominal": amount_paid,
                        "Bulan Terisi": months_str,
                        "Sisa Saldo": balance_after,
                        "Status": "APPROVED",
                        "Bukti URL": proof_cell_val,
                        "Approved By": tx["approved_by"] or "Bendahara",
                        "Keterangan": f"Via Bot ({payment_method})",
                    }
                    self.sheets_client.append_log_transaksi(log_data)

                    # Save filled cells & mark SYNCED
                    cells_json = json.dumps([{"coord": c} for c in recorded_cells])
                    conn.execute(
                        "UPDATE transactions SET sheets_cells_filled = ?, sync_status = 'SYNCED', "
                        "sync_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (cells_json, tx_id),
                    )
                    logger.info("Successfully synced approved transaction #%d to Sheets", tx_id)
                    return True

                # 3. Reverted Transaction Reverse Sync (Undo)
                elif tx_status == "REVERTED":
                    # Clear cells previously filled
                    raw_cells = tx["sheets_cells_filled"] or "[]"
                    cells_to_clear: list[str] = []
                    try:
                        parsed = json.loads(raw_cells)
                        for c in parsed:
                            if isinstance(c, dict) and "coord" in c:
                                cells_to_clear.append(c["coord"])
                            elif isinstance(c, dict) and "sheet" in c and "cell" in c:
                                cells_to_clear.append(f"'{c['sheet']}'!{c['cell']}")
                            elif isinstance(c, str):
                                cells_to_clear.append(c)
                    except Exception:
                        pass

                    if cells_to_clear:
                        self.sheets_client.batch_clear_cells(cells_to_clear)

                    # Restore balance in data_pengurus
                    self.sheets_client.update_member_balance(
                        phone=member_phone,
                        balance=tx["balance_before"],
                    )

                    # Append rollback note to log_transaksi
                    log_data = {
                        "Id": tx["id"],
                        "Timestamp": tx["updated_at"] or tx["created_at"],
                        "Nama": member_name,
                        "Nomor HP": member_phone,
                        "Nominal": -amount_paid,
                        "Bulan Terisi": "ROLLBACK",
                        "Sisa Saldo": tx["balance_before"],
                        "Status": "REVERTED",
                        "Bukti URL": proof_cell_val,
                        "Approved By": tx["approved_by"] or "Bendahara",
                        "Keterangan": "Transaksi Dibatalkan (/undo)",
                    }
                    self.sheets_client.append_log_transaksi(log_data)

                    conn.execute(
                        "UPDATE transactions SET sync_status = 'SYNCED', sync_error = NULL, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (tx_id,),
                    )
                    logger.info("Successfully reverse-synced reverted transaction #%d", tx_id)
                    return True

            except Exception as exc:
                err_msg = str(exc)
                logger.error("Failed to sync transaction #%d: %s", tx_id, err_msg)
                conn.execute(
                    "UPDATE transactions SET retry_count = retry_count + 1, sync_error = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (err_msg, tx_id),
                )
                return False

        return False

    async def process_pending_tasks(self) -> int:
        """Process all pending transactions requiring Google synchronization."""
        with self.db.connect() as conn:
            cursor = conn.execute(
                "SELECT id FROM transactions WHERE sync_status = 'PENDING' "
                "AND status IN ('APPROVED', 'REVERTED') ORDER BY id ASC"
            )
            rows = cursor.fetchall()
            tx_ids = [r[0] for r in rows]

        processed = 0
        for tx_id in tx_ids:
            success = await self.process_transaction(tx_id)
            if success:
                processed += 1
        return processed

    async def run_loop(
        self,
        poll_interval: float = 5.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run continuous outbox polling loop until stopped."""
        logger.info("Started OutboxWorker background polling loop")
        while stop_event is None or not stop_event.is_set():
            try:
                await self.process_pending_tasks()
            except Exception as exc:
                logger.error("Error in OutboxWorker polling loop: %s", exc)

            try:
                if stop_event:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                else:
                    await asyncio.sleep(poll_interval)
            except TimeoutError:
                pass
