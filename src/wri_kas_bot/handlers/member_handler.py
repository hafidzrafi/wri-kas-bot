"""Member self-service handler for Kas WRI Bot.

Handles member commands including /status (checking dues and balance),
/bayar (retrieving payment bank details and QRIS), and /help.
"""

import logging
from typing import Any

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository

logger = logging.getLogger(__name__)


async def handle_member(
    payload: WebhookPayload,
    db: Database,
    evolution: EvolutionClient | None = None,
) -> dict[str, Any]:
    """Process a member command or self-service query."""
    settings = get_settings()
    evo = evolution or EvolutionClient()

    sender_phone = payload.sender_phone
    sender_jid = payload.sender_jid

    with db.connect() as conn:
        member_repo = MemberRepository(conn)
        member = member_repo.get_by_phone(sender_phone)

    if not member or member["status"] != "Aktif":
        try:
            from wri_kas_bot.sync import sync_members_from_sheets

            synced = sync_members_from_sheets(db)
            if synced > 0:
                with db.connect() as conn:
                    member_repo = MemberRepository(conn)
                    member = member_repo.get_by_phone(sender_phone)
        except Exception as exc:
            logger.warning("On-demand member sync failed in member_handler: %s", exc)

    if not member or member["status"] != "Aktif":
        msg = (
            "Halo! Nomor WhatsApp Anda belum terdaftar sebagai anggota/pengurus aktif WRI. "
            "Silakan hubungi pengurus atau bendahara untuk pendataan akun."
        )
        await evo.send_text(recipient=sender_jid, text=msg)
        return {"status": "unregistered"}

    text = payload.text_body.strip().lower()

    if text in ("/status", "/cek", "status", "cek"):
        return await _handle_status(db, evo, member, sender_jid, settings)
    elif text in ("/bayar", "bayar"):
        return await _handle_bayar(db, evo, member, sender_jid, settings)
    elif text in ("/help", "/bantuan", "help", "bantuan"):
        return await _handle_help(evo, sender_jid)

    return await _handle_help(evo, sender_jid)


async def _handle_status(
    db: Database,
    evo: EvolutionClient,
    member: dict[str, Any],
    sender_jid: str,
    settings: Any,
) -> dict[str, Any]:
    """Return comprehensive member financial dues status card."""
    member_id = member["id"]
    with db.connect() as conn:
        dues_repo = MonthlyDuesRepository(conn)

        paid_dues = dues_repo.get_paid(member_id, year=2026)
        unpaid_dues = dues_repo.get_unpaid(member_id, year=2026)
        paid_months = [d.month for d in paid_dues]
        unpaid_months = [d.month for d in unpaid_dues]

        # Check for pending transaction awaiting verification
        pending_txs = conn.execute(
            "SELECT * FROM transactions WHERE member_id = ? AND status = 'PENDING' "
            "ORDER BY id DESC LIMIT 1",
            (member_id,),
        ).fetchall()
        pending_tx = pending_txs[0] if pending_txs else None

    unpaid_count = len(unpaid_months)
    due_per_month = settings.due_amount_per_month
    balance = member["balance"]
    net_bill = max(0, (unpaid_count * due_per_month) - balance)

    paid_str = ", ".join(paid_months) if paid_months else "Belum ada"
    unpaid_str = ", ".join(unpaid_months) if unpaid_months else "Lunas semua! 🎉"

    pending_notice = ""
    if pending_tx:
        amount = pending_tx["amount_paid"] or pending_tx["ocr_amount"] or 0
        pending_notice = (
            f"\n\n⏳ *Catatan:* Bukti transfer Anda sebesar Rp {amount:,} "
            f"sedang menunggu verifikasi bendahara."
        )

    msg = (
        f"📊 *STATUS KAS WRI (2026)*\n\n"
        f"👤 *Nama:* {member['name']}\n"
        f"📞 *No HP:* {member['phone_number']}\n"
        f"💳 *Saldo Kas Simpanan:* Rp {balance:,}\n\n"
        f"✅ *Bulan Lunas:* {paid_str}\n"
        f"📋 *Tunggakan:* {unpaid_str} ({unpaid_count} bln)\n"
        f"💰 *Total Tagihan Bersih:* Rp {net_bill:,}"
        f"{pending_notice}\n\n"
        f"Ketik */bayar* untuk melihat info rekening dan QRIS."
    )

    await evo.send_text(recipient=sender_jid, text=msg)
    return {"status": "ok", "unpaid_count": unpaid_count, "net_bill": net_bill}


async def _handle_bayar(
    db: Database,
    evo: EvolutionClient,
    member: dict[str, Any],
    sender_jid: str,
    settings: Any,
) -> dict[str, Any]:
    """Return payment methods, Bank Jago account, and QRIS URL."""
    member_id = member["id"]
    with db.connect() as conn:
        dues_repo = MonthlyDuesRepository(conn)
        unpaid = dues_repo.get_unpaid(member_id, year=2026)

    unpaid_count = len(unpaid)
    balance = member["balance"]
    net_bill = max(0, (unpaid_count * settings.due_amount_per_month) - balance)

    msg = (
        f"💳 *PEMBAYARAN KAS WRI*\n\n"
        f"Tagihan Anda saat ini: *Rp {net_bill:,}*\n\n"
        f"Silakan transfer ke salah satu rekening resmi WRI:\n\n"
        f"🏦 *Bank Jago*\n"
        f"• No. Rekening: *{settings.bank_account_number}*\n"
        f"• Atas Nama: *{settings.bank_account_holder}*\n\n"
        f"📱 *QRIS All Payment:*\n"
        f"{settings.qris_file_url}\n\n"
        f"📌 *Penting:* Setelah transfer berhasil, silakan langsung kirimkan "
        f"*FOTO / SCREENSHOT BUKTI TRANSFER* ke nomor bot ini. "
        f"Sistem AI akan otomatis membaca nominal dan meneruskan ke bendahara!"
    )

    await evo.send_text(recipient=sender_jid, text=msg)
    return {"status": "ok", "net_bill": net_bill}


async def _handle_help(evo: EvolutionClient, sender_jid: str) -> dict[str, Any]:
    """Return available commands help menu."""
    msg = (
        "🤖 *BANTUAN BOT KAS WRI*\n\n"
        "Berikut perintah yang dapat Anda gunakan:\n"
        "• */status* : Cek status tagihan, bulan lunas, dan saldo kas Anda\n"
        "• */bayar*  : Info rekening Bank Jago, QRIS, dan panduan transfer\n"
        "• *Kirim Foto Bukti*: Upload bukti transfer kas untuk verifikasi otomatis\n\n"
        "Untuk kendala pembayaran kas, hubungi bendahara WRI."
    )
    await evo.send_text(recipient=sender_jid, text=msg)
    return {"status": "ok"}
