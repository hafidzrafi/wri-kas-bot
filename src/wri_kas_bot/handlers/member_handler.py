"""Member self-service handler for Kas WRI Bot.

Handles member commands including /status (checking dues and balance),
/bayar (retrieving payment bank details and QRIS), and /help.
"""

import base64
import datetime
import logging
from pathlib import Path
from typing import Any

import httpx

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository

logger = logging.getLogger(__name__)

_CACHED_QRIS_BASE64: str | None = None


async def load_qris_image_base64(settings: Any) -> str | None:
    """Retrieve base64-encoded QRIS image from cache, local file, or remote URL."""
    global _CACHED_QRIS_BASE64
    if _CACHED_QRIS_BASE64:
        return _CACHED_QRIS_BASE64

    # Check local disk cache
    for local_path in (Path("data/qris.jpg"), Path("data/qris.png")):
        if local_path.is_file() and local_path.stat().st_size > 100:
            try:
                data = local_path.read_bytes()
                _CACHED_QRIS_BASE64 = base64.b64encode(data).decode("utf-8")
                return _CACHED_QRIS_BASE64
            except Exception as exc:
                logger.warning("Failed reading local QRIS file %s: %s", local_path, exc)

    url = getattr(settings, "qris_file_url", None)
    # Skip dummy example URLs during automated tests
    if url and "example.com" not in url and url.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 100:
                    _CACHED_QRIS_BASE64 = base64.b64encode(resp.content).decode("utf-8")
                    try:
                        qris_dest = Path("data/qris.jpg")
                        qris_dest.parent.mkdir(parents=True, exist_ok=True)
                        qris_dest.write_bytes(resp.content)
                    except Exception as err:
                        logger.debug("Could not cache QRIS to disk: %s", err)
                    return _CACHED_QRIS_BASE64
        except Exception as exc:
            logger.warning("Failed downloading QRIS image from %s: %s", url, exc)

    return None


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

        # Check for pending transaction awaiting verification
        pending_txs = conn.execute(
            "SELECT * FROM transactions WHERE member_id = ? AND status = 'PENDING' "
            "ORDER BY id DESC LIMIT 1",
            (member_id,),
        ).fetchall()
        pending_tx = pending_txs[0] if pending_txs else None

    today = datetime.date.today()
    current_year = today.year
    current_month_index = today.month

    # Dues tracking year: 2026
    if current_year > 2026:
        overdue_dues = unpaid_dues
        future_dues = []
    elif current_year < 2026:
        overdue_dues = []
        future_dues = unpaid_dues
    else:
        overdue_dues = [d for d in unpaid_dues if d.month_index <= current_month_index]
        future_dues = [d for d in unpaid_dues if d.month_index > current_month_index]

    overdue_count = len(overdue_dues)
    future_count = len(future_dues)
    due_per_month = settings.due_amount_per_month
    balance = member["balance"]
    net_bill = max(0, (overdue_count * due_per_month) - balance)

    paid_months = [d.month for d in paid_dues]
    paid_count = len(paid_months)
    paid_str = f"{', '.join(paid_months)} ({paid_count} bln)" if paid_count > 0 else "Belum ada"

    if overdue_count > 0:
        overdue_str = f"{', '.join(d.month for d in overdue_dues)} ({overdue_count} bln)"
    else:
        overdue_str = "Nihil (Lunas sampai bulan ini! 🎉)"

    if future_count > 0:
        future_str = f"{', '.join(d.month for d in future_dues)} ({future_count} bln)"
    else:
        future_str = "Nihil"

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
        f"📋 *Tunggakan Jatuh Tempo:* {overdue_str}\n"
        f"💰 *Total Tagihan Jatuh Tempo:* Rp {net_bill:,}\n"
        f"⏳ *Bulan Mendatang:* {future_str}"
        f"{pending_notice}\n\n"
        f"Ketik */bayar* untuk melihat info rekening dan QRIS."
    )

    await evo.send_text(recipient=sender_jid, text=msg)
    return {
        "status": "ok",
        "overdue_count": overdue_count,
        "future_count": future_count,
        "unpaid_count": len(unpaid_dues),
        "net_bill": net_bill,
    }


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

    today = datetime.date.today()
    current_year = today.year
    current_month_index = today.month

    if current_year > 2026:
        overdue_dues = unpaid
    elif current_year < 2026:
        overdue_dues = []
    else:
        overdue_dues = [d for d in unpaid if d.month_index <= current_month_index]

    overdue_count = len(overdue_dues)
    balance = member["balance"]
    net_bill = max(0, (overdue_count * settings.due_amount_per_month) - balance)

    if net_bill > 0:
        bill_section = f"Tagihan jatuh tempo Anda saat ini: *Rp {net_bill:,}*\n\n"
    else:
        fee = settings.due_amount_per_month
        bill_section = (
            f"Anda tidak memiliki tunggakan jatuh tempo saat ini (Saldo kas: Rp {balance:,}).\n"
            f"Jika ingin membayar kas bulan mendatang, kas per bulan adalah *Rp {fee:,}*.\n\n"
        )

    # Attempt to dispatch official QRIS image with caption
    qris_b64 = await load_qris_image_base64(settings)
    if qris_b64:
        caption = (
            f"💳 *PEMBAYARAN KAS WRI*\n\n"
            f"{bill_section}"
            f"Silakan transfer ke salah satu rekening resmi WRI:\n\n"
            f"🏦 *Bank Jago*\n"
            f"• No. Rekening: *{settings.bank_account_number}*\n"
            f"• Atas Nama: *{settings.bank_account_holder}*\n\n"
            f"📱 *QRIS All Payment:*\n"
            f"(Scan kode QR pada gambar di atas)\n\n"
            f"📌 *Penting:* Setelah transfer berhasil, silakan langsung kirimkan "
            f"*FOTO / SCREENSHOT BUKTI TRANSFER* ke nomor bot ini. "
            f"Sistem AI akan otomatis membaca nominal dan meneruskan ke bendahara!"
        )
        try:
            await evo.send_image_base64(
                jid=sender_jid,
                image_base64=qris_b64,
                caption=caption,
                file_name="qris.jpg",
            )
            return {"status": "ok", "net_bill": net_bill, "media": "image"}
        except Exception as exc:
            logger.warning("Failed sending QRIS image: %s, falling back to text", exc)

    # Fallback to plain text message
    msg = (
        f"💳 *PEMBAYARAN KAS WRI*\n\n"
        f"{bill_section}"
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
    return {"status": "ok", "net_bill": net_bill, "media": "text"}


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
