"""Inbound receipt handling module for Kas WRI Bot.

Downloads receipt images, executes Gemini Flash Vision OCR extraction,
stores draft transactions in SQLite, acknowledges members, and forwards
base64 verification cards to the treasurer.
"""

import base64
import datetime
import logging
from pathlib import Path
from typing import Any

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.drive import format_drive_filename
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.ocr import ReceiptOCR
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository, TransactionRepository

logger = logging.getLogger(__name__)


async def handle_receipt(
    payload: WebhookPayload,
    db: Database,
    evolution: EvolutionClient | None = None,
    ocr_engine: ReceiptOCR | None = None,
) -> dict[str, Any]:
    """Process an inbound receipt image from a WhatsApp member."""
    settings = get_settings()
    evo = evolution or EvolutionClient()
    ocr = ocr_engine or ReceiptOCR(api_key=settings.gemini_api_key, model=settings.gemini_model)

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
            logger.warning("On-demand member sync failed in receipt_handler: %s", exc)

    if not member or member["status"] != "Aktif":
        msg = (
            "Halo! Nomor WhatsApp Anda belum terdaftar sebagai anggota aktif WRI. "
            "Silakan hubungi pengurus atau bendahara untuk registrasi data anggota."
        )
        await evo.send_text(recipient=sender_jid, text=msg)
        return {"status": "unregistered"}

    # 2. Extract or Download Image Bytes
    image_bytes: bytes = b""
    b64_str = payload.image_bytes_base64
    if b64_str:
        try:
            image_bytes = base64.b64decode(b64_str)
        except Exception as exc:
            logger.warning("Failed decoding image base64: %s", exc)

    if not image_bytes:
        msg_data = payload.data.message
        media_url = getattr(msg_data, "media_url", None) if msg_data else None
        if media_url:
            try:
                image_bytes = await evo.download_media(media_url)
            except Exception as exc:
                logger.warning("Could not download media from %s: %s", media_url, exc)

    if not image_bytes:
        logger.warning("Received receipt message with no extractable image bytes")
        image_bytes = b""

    # 3. Save Receipt to Local Disk (Strict sanitized path)
    receipts_dir = Path(settings.receipts_dir)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    ts = payload.data.message_timestamp or 1700000000
    safe_filename = format_drive_filename(sender_phone, member["name"], str(ts))
    file_path = receipts_dir / safe_filename
    try:
        if image_bytes:
            file_path.write_bytes(image_bytes)
    except Exception as exc:
        logger.error("Failed saving receipt image to %s: %s", file_path, exc)

    # 4. Multimodal AI Extraction (Gemini Flash Vision)
    ocr_amount: int | None = None
    ocr_bank: str | None = None
    try:
        ocr_result = await ocr.extract_receipt(image_bytes)
        ocr_amount = ocr_result.nominal
        ocr_bank = ocr_result.bank
    except Exception as exc:
        logger.warning("OCR extraction failed for receipt: %s", exc)

    # 5. Insert Draft Transaction in SQLite
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)
        unpaid = dues_repo.get_unpaid(member["id"], year=2026)

        tx_id = tx_repo.create_draft(
            member_id=member["id"],
            amount_paid=ocr_amount or 0,
            payment_method="TRANSFER_JAGO",
            local_image_path=str(file_path),
            ocr_amount=ocr_amount,
            ocr_bank=ocr_bank,
            wa_message_id=payload.message_id,
        )

    # 6. Immediate Acknowledgment to Member
    ack_msg = "Bukti transfer diterima. Menunggu verifikasi bendahara."
    await evo.send_text(recipient=sender_jid, text=ack_msg)

    # 7. Forward Verification Card to Treasurer
    today = datetime.date.today()
    current_year = today.year
    current_month_index = today.month

    if current_year > 2026:
        overdue_dues = unpaid
    elif current_year < 2026:
        overdue_dues = []
    else:
        overdue_dues = [
            d
            for d in unpaid
            if (d["month_index"] if isinstance(d, dict) else d.month_index) <= current_month_index
        ]

    overdue_months = [(d["month"] if isinstance(d, dict) else d.month) for d in overdue_dues]
    overdue_count = len(overdue_months)
    if overdue_count > 0:
        overdue_str = f"{', '.join(overdue_months)} ({overdue_count} bln)"
    else:
        overdue_str = "Nihil (Lunas s/d bulan ini)"

    due_per_month = settings.due_amount_per_month
    balance = member["balance"]
    net_due = max(0, (overdue_count * due_per_month) - balance)

    ocr_display = f"Rp {ocr_amount:,}" if ocr_amount else "Tidak terbaca (Isi manual)"
    bank_display = ocr_bank or "Bank/E-Wallet"

    caption = (
        f"*BUKTI KAS MASUK*\n\n"
        f"Nama: {member['name']}\n"
        f"No: {member['phone_number']}\n"
        f"Saldo: Rp {balance:,}\n"
        f"Tunggakan: {overdue_str}\n"
        f"Tagihan: Rp {net_due:,}\n\n"
        f"Nominal terbaca: {ocr_display} ({bank_display})\n\n"
        f"Balas pesan ini:\n"
        f"/acc\n"
        f"/acc [nominal]\n"
        f"/reject [alasan]"
    )

    media_b64 = base64.b64encode(image_bytes).decode("utf-8")
    notif_res = await evo.send_media(
        recipient=settings.bendahara_jid,
        media=media_b64,
        caption=caption,
        media_type="image",
        file_name=file_path.name,
    )

    # Track treasurer message id for swipe-reply correlation
    notif_msg_id = notif_res.get("key", {}).get("id") if isinstance(notif_res, dict) else None
    if notif_msg_id:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE transactions SET notification_msg_id = ? WHERE id = ?",
                (notif_msg_id, tx_id),
            )

    return {"status": "ok", "transaction_id": tx_id}
