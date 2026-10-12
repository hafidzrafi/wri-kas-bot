"""Google Sheets synchronization client for Kas WRI Bot.

Handles cell updating, audit log appending with exact header mapping,
batch clearing on rollbacks, and bottom-up Debit_Kredit ledger parsing.
"""

import logging
import re
from pathlib import Path
from typing import Any

import googleapiclient.discovery
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_PENGURUS_MATRIX = "PENGURUS-2025/2026"
SHEET_DATA_PENGURUS = "data_pengurus"
SHEET_LOG_TRANSAKSI = "log_transaksi"
SHEET_DEBIT_KREDIT = "Debit_Kredit"

LOG_TRANSAKSI_HEADERS = [
    "Id",
    "Timestamp",
    "Nama",
    "Nomor HP",
    "Nominal",
    "Bulan Terisi",
    "Sisa Saldo",
    "Status",
    "Bukti URL",
    "Approved By",
    "Keterangan",
]


def col_to_letter(col_idx: int) -> str:
    """Convert 1-based column index to spreadsheet column letter(s).

    1 -> A, 26 -> Z, 27 -> AA.
    """
    result = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


class GoogleSheetsClient:
    """Client for interacting with Google Sheets API v4."""

    def __init__(
        self,
        service_account_json: str | None = None,
        spreadsheet_id: str | None = None,
    ) -> None:
        self.service_account_json = service_account_json or "credentials.json"
        self.spreadsheet_id = spreadsheet_id or "mock-spreadsheet-id"
        self._service: Any = None

    @property
    def service(self) -> Any:
        """Lazily builds and caches the Google Sheets v4 API service."""
        if self._service is None:
            creds_path = Path(self.service_account_json)
            credentials = None
            if creds_path.is_file():
                try:
                    credentials = service_account.Credentials.from_service_account_file(
                        str(creds_path),
                        scopes=SHEETS_SCOPES,
                    )
                except Exception:
                    credentials = None

            if credentials is not None:
                self._service = googleapiclient.discovery.build(
                    "sheets", "v4", credentials=credentials, cache_discovery=False
                )
            else:
                self._service = googleapiclient.discovery.build(
                    "sheets", "v4", cache_discovery=False
                )
        return self._service

    def _get_values(self, sheet_range: str) -> list[list[Any]]:
        """Retrieve 2D matrix of values from spreadsheet range."""
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=sheet_range)
            .execute()
        )
        return result.get("values", [])

    def update_payment_month(self, name: str, month: str, proof_url: str) -> str:
        """Update payment cell for a member in PENGURUS-2025/2026 matrix.

        Returns coordinate string e.g. 'PENGURUS-2025/2026!D5'.
        """
        rows = self._get_values(f"{SHEET_PENGURUS_MATRIX}!A1:Z60")
        if not rows:
            # Fallback coordinate if sheet empty
            coord = f"{SHEET_PENGURUS_MATRIX}!D5"
            return coord

        headers = [str(c).strip().lower() for c in rows[0]]
        target_month = month.strip().lower()
        if target_month not in headers:
            col_letter = "D"
        else:
            col_letter = col_to_letter(headers.index(target_month) + 1)

        target_row_idx = 5
        target_name = name.strip().lower()
        for idx, row in enumerate(rows[1:], start=2):
            if row and len(row) > 0 and str(row[0]).strip().lower() == target_name:
                target_row_idx = idx
                break

        coord = f"{SHEET_PENGURUS_MATRIX}!{col_letter}{target_row_idx}"
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=coord,
            valueInputOption="USER_ENTERED",
            body={"values": [[proof_url]]},
        ).execute()

        logger.info("Updated dues cell %s for member '%s' to '%s'", coord, name, proof_url)
        return coord

    def batch_clear_cells(self, cell_ranges: list[str]) -> None:
        """Clear list of spreadsheet cell ranges in a single batchClear API call."""
        if not cell_ranges:
            return

        # Deduplicate ranges while preserving order
        unique_ranges: list[str] = []
        for r in cell_ranges:
            if r and r not in unique_ranges:
                unique_ranges.append(r)

        if not unique_ranges:
            return

        self.service.spreadsheets().values().batchClear(
            spreadsheetId=self.spreadsheet_id,
            body={"ranges": unique_ranges},
        ).execute()

        logger.info("Executed batchClear on %d cell ranges: %s", len(unique_ranges), unique_ranges)

    def append_log_transaksi(self, row_data: dict[str, Any]) -> None:
        """Append audit transaction entry with exact header key matching."""
        row_values = [row_data.get(header, "") for header in LOG_TRANSAKSI_HEADERS]
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_LOG_TRANSAKSI}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row_values]},
        ).execute()

        logger.info("Appended transaction %s to %s", row_data.get("Id"), SHEET_LOG_TRANSAKSI)

    def update_member_balance(self, phone: str, balance: int) -> None:
        """Update member balance cell in data_pengurus sheet."""
        rows = self._get_values(f"{SHEET_DATA_PENGURUS}!A1:Z100")
        if not rows or len(rows) < 2:
            return

        headers = [str(c).strip().lower() for c in rows[0]]
        phone_col_idx = -1
        saldo_col_idx = -1

        for idx, h in enumerate(headers):
            if "hp" in h or "telepon" in h:
                phone_col_idx = idx
            elif "saldo" in h:
                saldo_col_idx = idx

        if saldo_col_idx == -1:
            return

        clean_target_phone = re.sub(r"\D", "", phone)
        saldo_letter = col_to_letter(saldo_col_idx + 1)

        for row_idx, row in enumerate(rows[1:], start=2):
            if phone_col_idx != -1 and len(row) > phone_col_idx:
                row_phone = re.sub(r"\D", "", str(row[phone_col_idx]))
                if row_phone and clean_target_phone in row_phone:
                    coord = f"{SHEET_DATA_PENGURUS}!{saldo_letter}{row_idx}"
                    self.service.spreadsheets().values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=coord,
                        valueInputOption="USER_ENTERED",
                        body={"values": [[balance]]},
                    ).execute()
                    logger.info("Updated %s to balance %d for phone %s", coord, balance, phone)
                    return

    def scan_debit_kredit_bottom_up(self) -> int:
        """Scan Debit_Kredit bottom-up skipping spacer rows 55-74 and summary row 75.

        Returns 1-based row index of the last valid data entry.
        """
        rows = self._get_values(f"{SHEET_DEBIT_KREDIT}!A1:G100")
        if not rows:
            return 54

        # Traverse upwards from min(len(rows), 74) to skip row 75 totals
        max_idx = min(len(rows), 74)
        for row_idx in range(max_idx, 1, -1):
            if 55 <= row_idx <= 74:
                # Skip spacer rows 55-74
                continue

            row_data = rows[row_idx - 1]
            if not row_data:
                continue

            tanggal = str(row_data[0]).strip() if len(row_data) > 0 else ""
            # Col 5 (index 4) is SALDO
            saldo = str(row_data[4]).strip() if len(row_data) > 4 else ""

            if tanggal and saldo:
                return row_idx

        return 54

    def append_debit_kredit_summary(
        self,
        period_month: str,
        non_cash_amount: int = 0,
        cash_amount: int = 0,
    ) -> None:
        """Append monthly non-cash and cash summary rows to Debit_Kredit."""
        last_row = self.scan_debit_kredit_bottom_up()
        next_row = last_row + 1

        rows_to_append: list[list[Any]] = []
        if non_cash_amount > 0:
            rows_to_append.append(
                [
                    period_month,
                    f"Kas Pengurus Bulan {period_month} (Non Tunai)",
                    non_cash_amount,
                    0,
                    f"=E{next_row - 1}+C{next_row}",
                    "Jago",
                    "",
                ]
            )
            next_row += 1

        if cash_amount > 0:
            rows_to_append.append(
                [
                    period_month,
                    f"Kas Pengurus Bulan {period_month} (Tunai)",
                    cash_amount,
                    0,
                    f"=E{next_row - 1}+C{next_row}",
                    "Tunai",
                    "",
                ]
            )

        if rows_to_append:
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{SHEET_DEBIT_KREDIT}!A{last_row + 1}",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows_to_append},
            ).execute()
            logger.info("Appended %d summary rows to %s", len(rows_to_append), SHEET_DEBIT_KREDIT)

    def fetch_all_members(self) -> list[dict[str, Any]]:
        """Retrieve all registered members from data_pengurus sheet.

        Returns list of dicts with keys: name, phone_number, status, deposit_balance.
        """
        rows = self._get_values(f"{SHEET_DATA_PENGURUS}!A1:E100")
        if not rows or len(rows) < 2:
            return []

        headers = [str(c).strip().lower() for c in rows[0]]

        def _find_idx(candidates: tuple[str, ...], default: int) -> int:
            for cand in candidates:
                if cand in headers:
                    return headers.index(cand)
            return default

        name_idx = _find_idx(("nama", "name"), 0)
        phone_idx = _find_idx(("no hp", "nomor hp", "phone"), 1)
        status_idx = _find_idx(("status", "keaktifan"), 2)
        saldo_idx = _find_idx(("saldo", "balance"), 3)

        members: list[dict[str, Any]] = []
        for r in rows[1:]:
            if len(r) > max(name_idx, phone_idx):
                name = str(r[name_idx]).strip()
                raw_phone = re.sub(r"\D", "", str(r[phone_idx]).strip())
                if not raw_phone:
                    continue
                if raw_phone.startswith("08"):
                    phone = "62" + raw_phone[1:]
                elif raw_phone.startswith("8"):
                    phone = "62" + raw_phone
                else:
                    phone = raw_phone

                raw_status = str(r[status_idx]).strip().lower() if len(r) > status_idx else "aktif"
                status = "Aktif" if raw_status == "aktif" else "Nonaktif"

                saldo = 0
                if len(r) > saldo_idx:
                    try:
                        digits = re.sub(r"\D", "", str(r[saldo_idx]))
                        saldo = int(digits) if digits else 0
                    except ValueError:
                        saldo = 0

                if name and phone:
                    members.append(
                        {
                            "name": name,
                            "phone_number": phone,
                            "status": status,
                            "deposit_balance": saldo,
                        }
                    )
        return members

    def fetch_payment_matrix(self) -> dict[str, dict[str, str]]:
        """Retrieve existing payment records from PENGURUS-2025/2026 sheet.

        Returns mapping of normalized member name -> dict of canonical month name to cell value.
        e.g. {'mohammad hafidz rafi rabbani': {'Februari': 'Bank Jago', 'Maret': 'Bank Jago'}}
        """
        rows = self._get_values(f"{SHEET_PENGURUS_MATRIX}!A1:N60")
        if not rows or len(rows) < 2:
            return {}

        headers = [str(c).strip().lower() for c in rows[0]]
        month_name_map = {
            "februari": "Februari",
            "february": "Februari",
            "maret": "Maret",
            "march": "Maret",
            "april": "April",
            "mei": "Mei",
            "may": "Mei",
            "juni": "Juni",
            "june": "Juni",
            "juli": "Juli",
            "july": "Juli",
            "agustus": "Agustus",
            "august": "Agustus",
            "september": "September",
            "oktober": "Oktober",
            "october": "Oktober",
            "november": "November",
            "desember": "Desember",
            "december": "Desember",
        }

        month_col_map: dict[int, str] = {}
        for idx, h in enumerate(headers):
            canonical = month_name_map.get(h)
            if canonical:
                month_col_map[idx] = canonical

        name_col_idx = 0
        for idx, h in enumerate(headers):
            if h in ("nama", "name"):
                name_col_idx = idx
                break

        matrix: dict[str, dict[str, str]] = {}
        for r in rows[1:]:
            if len(r) > name_col_idx:
                raw_name = str(r[name_col_idx]).strip()
                if not raw_name:
                    continue
                clean_name = re.sub(r"[^\w\s]", "", raw_name.lower())
                norm_name = " ".join(clean_name.split())

                payments: dict[str, str] = {}
                for col_idx, month_name in month_col_map.items():
                    if len(r) > col_idx:
                        val = str(r[col_idx]).strip()
                        if val:
                            payments[month_name] = val
                matrix[norm_name] = payments
        return matrix
