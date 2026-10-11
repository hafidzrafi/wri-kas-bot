"""Automated billing blast and ledger aggregation scheduler for Kas WRI Bot.

Dispatches monthly dues reminder messages on the 1st of every month at 19:00 WIB
with randomized anti-spam jitter delay (30-60s) and per-member last_billed_at tracking.
"""

import asyncio
import datetime
import logging
import random
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository
from wri_kas_bot.sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)


def get_random_jitter(min_delay: float = 30.0, max_delay: float = 60.0) -> float:
    """Return randomized jitter delay in seconds to mitigate WhatsApp rate-limits."""
    return random.uniform(min_delay, max_delay)


async def execute_billing_blast(
    db: Database,
    evolution: EvolutionClient | None = None,
    target_date: datetime.date | None = None,
    delay_between_messages: bool = True,
) -> int:
    """Execute monthly dues billing blast for delinquent active members.

    Returns count of successfully notified members.
    """
    settings = get_settings()
    evo = evolution or EvolutionClient()
    today = target_date or datetime.date.today()
    current_year = today.year
    current_month_index = today.month

    # Beginning of current calendar month formatted with space for SQLite timestamp comparison
    cycle_start_str = f"{current_year:04d}-{current_month_index:02d}-01 00:00:00"

    dispatched_count = 0
    with db.connect() as conn:
        member_repo = MemberRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)
        active_members = member_repo.list_active()

    for member in active_members:
        member_id = member["id"]
        last_billed = member["last_billed_at"]

        # Anti-spam guard: skip if member was already billed in current month cycle
        if last_billed and str(last_billed) >= cycle_start_str:
            logger.info("Skipping member %s; already billed at %s", member["name"], last_billed)
            continue

        with db.connect() as conn:
            dues_repo = MonthlyDuesRepository(conn)
            all_unpaid = dues_repo.get_unpaid(member_id, year=current_year)

        # Include delinquent months up to and including current calendar month
        eligible_unpaid = [d for d in all_unpaid if d["month_index"] <= current_month_index]
        if not eligible_unpaid:
            continue

        unpaid_count = len(eligible_unpaid)
        balance = member["balance"]
        due_amount = settings.due_amount_per_month
        net_bill = max(0, (unpaid_count * due_amount) - balance)

        if net_bill <= 0:
            # Fully covered by balance deposit
            continue

        unpaid_names = ", ".join(d["month"] for d in eligible_unpaid)
        msg = (
            f"🔔 *PENGINGAT KAS WRI (BULAN {today.strftime('%B %Y').upper()})*\n\n"
            f"Halo {member['name']},\n"
            f"Ini adalah pengingat pembayaran uang kas pengurus WRI.\n\n"
            f"📋 *Tunggakan Anda:* {unpaid_names} ({unpaid_count} bulan)\n"
            f"💳 *Saldo Kas Anda:* Rp {balance:,}\n"
            f"💰 *Total Tagihan Bersih:* Rp {net_bill:,}\n\n"
            f"Silakan transfer ke rekening resmi Bank Jago: *{settings.bank_account_number}* "
            f"({settings.bank_account_holder}). "
            f"Setelah transfer, kirimkan foto bukti pembayaran ke nomor ini "
            f"untuk verifikasi otomatis! ✨"
        )

        try:
            await evo.send_text(recipient=member["phone_number"], text=msg)
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE members SET last_billed_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (member_id,),
                )
            dispatched_count += 1
            logger.info(
                "Successfully sent monthly bill to %s (%s)",
                member["name"],
                member["phone_number"],
            )

            if delay_between_messages:
                jitter = get_random_jitter()
                await asyncio.sleep(jitter)

        except Exception as exc:
            logger.error("Failed sending billing blast to member %s: %s", member["name"], exc)

    return dispatched_count


async def execute_monthly_ledger_sync(
    db: Database,
    sheets_client: GoogleSheetsClient | None = None,
    period_date: datetime.date | None = None,
) -> dict[str, Any]:
    """Aggregate previous month dues and append summary row to Debit_Kredit sheet."""
    today = period_date or datetime.date.today()
    # Calculate previous month period
    first_of_this_month = today.replace(day=1)
    prev_month_last_day = first_of_this_month - datetime.timedelta(days=1)
    prev_month_first_day = prev_month_last_day.replace(day=1)
    prev_month_name = prev_month_last_day.strftime("%B")

    start_date_str = f"{prev_month_first_day.strftime('%Y-%m-%d')} 00:00:00"
    end_date_str = f"{prev_month_last_day.strftime('%Y-%m-%d')} 23:59:59"

    with db.connect() as conn:
        cursor = conn.execute(
            """
            SELECT payment_method, SUM(amount_paid) FROM transactions
            WHERE status = 'APPROVED'
              AND created_at >= ? AND created_at <= ?
            GROUP BY payment_method
            """,
            (start_date_str, end_date_str),
        )
        totals = {r[0]: r[1] or 0 for r in cursor.fetchall()}

    cash_amount = totals.get("CASH", 0)
    non_cash_amount = sum(v for k, v in totals.items() if k != "CASH")

    if sheets_client:
        sheets_client.append_debit_kredit_summary(
            period_month=prev_month_name,
            non_cash_amount=non_cash_amount,
            cash_amount=cash_amount,
        )

    return {
        "prev_month": prev_month_name,
        "cash_amount": cash_amount,
        "non_cash_amount": non_cash_amount,
    }


def setup_scheduler(
    db: Database,
    evolution: EvolutionClient | None = None,
    sheets: GoogleSheetsClient | None = None,
) -> AsyncIOScheduler:
    """Configure APScheduler async cron jobs."""
    settings = get_settings()
    sched = AsyncIOScheduler(timezone="Asia/Jakarta")

    # Run on 1st of every month at configured hour and minute (19:00 WIB default)
    @sched.scheduled_job(
        "cron",
        day=1,
        hour=settings.billing_hour,
        minute=settings.billing_minute,
        name="monthly_dues_billing_blast",
    )
    async def _monthly_job():
        logger.info("Triggering scheduled monthly dues billing blast and ledger sync")
        await execute_billing_blast(db=db, evolution=evolution)
        await execute_monthly_ledger_sync(db=db, sheets_client=sheets)

    return sched
