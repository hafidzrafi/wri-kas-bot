"""Member and dues synchronization module between Google Sheets and local SQLite.

Imports and updates active members from the data_pengurus Google Sheet into
the local SQLite database, initializing annual monthly dues tracking.
"""

import logging
from pathlib import Path

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository
from wri_kas_bot.sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)


def sync_members_from_sheets(
    db: Database,
    sheets_client: GoogleSheetsClient | None = None,
    year: int = 2026,
) -> int:
    """Sync registered members from Google Sheets data_pengurus to SQLite.

    Returns the count of successfully synchronized members.
    """
    settings = get_settings()
    creds_file = Path(settings.google_service_account_json)
    if sheets_client is None and not creds_file.is_file():
        logger.debug("Credentials file not found (%s), skipping remote sync", creds_file)
        return 0

    sheets = sheets_client or GoogleSheetsClient(
        service_account_json=str(creds_file),
        spreadsheet_id=settings.google_spreadsheet_id,
    )
    try:
        remote_members = sheets.fetch_all_members()
    except Exception as exc:
        logger.warning("Failed fetching members from Google Sheets: %s", exc)
        return 0

    if not remote_members:
        logger.info("No members returned from Google Sheets data_pengurus.")
        return 0

    synced_count = 0
    with db.transaction() as conn:
        member_repo = MemberRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)

        for m in remote_members:
            phone = m["phone_number"]
            name = m["name"]
            status = m.get("status", "Aktif")
            deposit_balance = m.get("deposit_balance", 0)

            existing = member_repo.get_by_phone(phone)
            if existing is None:
                member_id = member_repo.create(
                    name=name,
                    phone_number=phone,
                    status=status,
                    balance=deposit_balance,
                )
                logger.debug("Created member %s (%s) id=%d", name, phone, member_id)
            else:
                member_id = existing.id
                conn.execute(
                    "UPDATE members SET name = ?, status = ? WHERE id = ?",
                    (name, status, member_id),
                )
                logger.debug("Updated member %s (%s) id=%d", name, phone, member_id)

            # Initialize 2026 dues (Februari - Desember)
            dues_repo.init_member_dues(member_id, year=year)
            synced_count += 1

    logger.info("Successfully synced %d members from Google Sheets to SQLite", synced_count)
    return synced_count
