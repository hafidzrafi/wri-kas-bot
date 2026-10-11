"""Treasurer moderation handler for Kas WRI Bot.

Processes moderation commands including /acc (approval), /reject, /tunai (cash dues),
/undo (rollback), /koreksi (reallocation), and manual balance adjustments.
"""

import logging
from typing import Any

from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.fifo import MONTH_INDEX_TO_NAME, allocate_dues
from wri_kas_bot.models import WebhookPayload, parse_amount
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository, TransactionRepository
from wri_kas_bot.undo import execute_koreksi, execute_undo

logger = logging.getLogger(__name__)


async def handle_treasurer(
    payload: WebhookPayload,
    db: Database,
    evolution: EvolutionClient | None = None,
) -> dict[str, Any]:
    """Process a moderation command from the active treasurer."""
    settings = get_settings()
    evo = evolution or EvolutionClient()

    sender_phone = payload.sender_phone
    sender_jid = payload.sender_jid

    # 1. Authorization Guard (Strict canonical equality)
    if sender_phone != settings.bendahara_number and sender_jid != settings.bendahara_jid:
        logger.warning("Unauthorized moderation attempt from %s", sender_jid)
        return {"status": "unauthorized"}

    raw_text = payload.text_body.strip()
    cmd = raw_text.split()[0].lower() if raw_text else ""

    # Quoted context extraction for swipe-reply
    quoted_id: str | None = payload.quoted_stanza_id

    # 2. Command: /acc or /approve
    if cmd in ("/acc", "/approve"):
        return await _handle_acc(db, evo, payload, raw_text, quoted_id, settings)

    # 3. Command: /reject
    elif cmd in ("/reject", "/tolak"):
        return await _handle_reject(db, evo, raw_text, quoted_id, settings)

    # 4. Command: /tunai or /cash
    elif cmd in ("/tunai", "/cash"):
        return await _handle_cash(db, evo, raw_text, settings)

    # 5. Command: /undo
    elif cmd == "/undo":
        return await _handle_undo(db, evo, quoted_id, settings)

    # 6. Command: /koreksi
    elif cmd == "/koreksi":
        return await _handle_koreksi(db, evo, raw_text, quoted_id, settings)

    # 7. Command: /tambahsaldo
    elif cmd == "/tambahsaldo":
        return await _handle_tambah_saldo(db, evo, raw_text, settings)

    # 8. Command: /setsaldo
    elif cmd == "/setsaldo":
        return await _handle_set_saldo(db, evo, raw_text, settings)

    return {"status": "ignored"}


async def _handle_acc(
    db: Database,
    evo: EvolutionClient,
    payload: WebhookPayload,
    text: str,
    quoted_id: str | None,
    settings: Any,
) -> dict[str, Any]:
    """Handle /acc approval swipe-reply."""
    parts = text.split()
    explicit_nominal: int | None = None
    if len(parts) > 1:
        explicit_nominal = parse_amount(parts[1])

    with db.connect() as conn:
        tx_repo = TransactionRepository(conn)
        tx = None
        if quoted_id:
            tx = tx_repo.get_by_notification_msg_id(quoted_id)
        if not tx:
            # Fallback to latest pending transaction
            rows = conn.execute(
                "SELECT * FROM transactions WHERE status = 'PENDING' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            tx = rows[0] if rows else None

    if not tx:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="❌ Tidak ada transaksi PENDING yang sesuai untuk di-acc.",
        )
        return {"status": "not_found"}

    final_nominal = explicit_nominal or tx["ocr_amount"]
    if not final_nominal or final_nominal <= 0:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="⚠️ Nominal OCR tidak terdeteksi. Silakan ketik: /acc [nominal]",
        )
        return {"status": "nominal_required"}

    # Allocate dues using FIFO engine
    tx_id = tx["id"]
    member_id = tx["member_id"]
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        member_repo = MemberRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)
        member = member_repo.get_by_id(member_id)
        if not member:
            return {"status": "member_not_found"}

        unpaid_dues = dues_repo.get_unpaid(member_id, year=2026)
        alloc_result = allocate_dues(
            unpaid_months=unpaid_dues,
            amount_paid=final_nominal,
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
                    payment_method=tx["payment_method"],
                    proof_url=tx["proof_drive_url"] or "",
                )

        months_list = alloc_result.cleared_month_names
        member_repo.update_balance(member_id, alloc_result.balance_after)
        tx_repo.approve(
            tx_id=tx_id,
            balance_before=alloc_result.balance_before,
            balance_after=alloc_result.balance_after,
            months_filled=months_list,
            approved_by=settings.bendahara_jid,
        )

    # Notify Member
    months_str = ", ".join(months_list) if months_list else "Belum mencukupi 1 bulan (masuk saldo)"
    member_msg = (
        f"✅ *PEMBAYARAN KAS DISETUJUI!*\n\n"
        f"Halo {member['name']},\n"
        f"Pembayaran kas Anda sebesar *Rp {final_nominal:,}* telah diverifikasi oleh bendahara.\n\n"
        f"📋 *Bulan Terbayar:* {months_str}\n"
        f"💳 *Sisa Saldo Kas:* Rp {alloc_result.balance_after:,}\n\n"
        f"Terima kasih atas kontribusinya untuk WRI! ✨"
    )
    await evo.send_text(recipient=member["phone_number"], text=member_msg)

    # Confirm to Treasurer
    treasurer_msg = (
        f"✅ Transaksi #{tx_id} ({member['name']}) berhasil di-acc.\n"
        f"Nominal: Rp {final_nominal:,}\n"
        f"Bulan terisi: {months_str}\n"
        f"Sisa saldo: Rp {alloc_result.balance_after:,}"
    )
    await evo.send_text(recipient=settings.bendahara_jid, text=treasurer_msg)
    return {"status": "approved", "transaction_id": tx_id}


async def _handle_reject(
    db: Database,
    evo: EvolutionClient,
    text: str,
    quoted_id: str | None,
    settings: Any,
) -> dict[str, Any]:
    """Handle /reject swipe-reply."""
    parts = text.split(maxsplit=1)
    reason = parts[1].strip() if len(parts) > 1 else "Bukti transfer tidak valid/tidak terbaca"

    with db.connect() as conn:
        tx_repo = TransactionRepository(conn)
        tx = None
        if quoted_id:
            tx = tx_repo.get_by_notification_msg_id(quoted_id)
        if not tx:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE status = 'PENDING' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            tx = rows[0] if rows else None

    if not tx:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="❌ Tidak ada transaksi PENDING yang ditemukan untuk ditolak.",
        )
        return {"status": "not_found"}

    tx_id = tx["id"]
    member_id = tx["member_id"]
    with db.transaction() as conn:
        tx_repo = TransactionRepository(conn)
        member_repo = MemberRepository(conn)
        member = member_repo.get_by_id(member_id)
        tx_repo.reject(tx_id=tx_id, reason=reason, approved_by=settings.bendahara_jid)

    member_msg = (
        f"❌ *BUKTI KAS DITOLAK*\n\n"
        f"Halo {member['name']},\n"
        f"Bukti pembayaran kas Anda belum dapat disetujui oleh bendahara.\n\n"
        f"📝 *Alasan:* {reason}\n\n"
        f"Silakan upload ulang foto bukti transfer yang jelas dan valid. Terima kasih! 🙏"
    )
    await evo.send_text(recipient=member["phone_number"], text=member_msg)

    treasurer_msg = f"❌ Transaksi #{tx_id} ({member['name']}) telah ditolak.\nAlasan: {reason}"
    await evo.send_text(recipient=settings.bendahara_jid, text=treasurer_msg)
    return {"status": "rejected", "transaction_id": tx_id}


async def _handle_cash(
    db: Database,
    evo: EvolutionClient,
    text: str,
    settings: Any,
) -> dict[str, Any]:
    """Handle /tunai <phone> <nominal> direct cash dues allocation."""
    parts = text.split()
    if len(parts) < 3:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text=(
                "Format salah. Gunakan: /tunai [nomor_hp] [nominal]\n"
                "Contoh: /tunai 6281100000001 20000"
            ),
        )
        return {"status": "invalid_syntax"}

    phone_input = parts[1]
    nominal = parse_amount(parts[2])
    if not nominal or nominal <= 0:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="Nominal tidak valid. Contoh: /tunai 6281100000001 20000",
        )
        return {"status": "invalid_nominal"}

    with db.transaction() as conn:
        member_repo = MemberRepository(conn)
        tx_repo = TransactionRepository(conn)
        dues_repo = MonthlyDuesRepository(conn)
        member = member_repo.get_by_phone(phone_input)

        if not member:
            await evo.send_text(
                recipient=settings.bendahara_jid,
                text=f"Anggota dengan nomor '{phone_input}' tidak ditemukan.",
            )
            return {"status": "member_not_found"}

        member_id = member["id"]
        tx_id = tx_repo.create_draft(
            member_id=member_id,
            amount_paid=nominal,
            payment_method="CASH",
            local_image_path="",
            ocr_amount=nominal,
            ocr_bank="Tunai",
        )

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
                    payment_method="CASH",
                    proof_url="Tunai",
                )

        months_list = alloc_result.cleared_month_names
        member_repo.update_balance(member_id, alloc_result.balance_after)
        tx_repo.approve(
            tx_id=tx_id,
            balance_before=alloc_result.balance_before,
            balance_after=alloc_result.balance_after,
            months_filled=months_list,
            approved_by=settings.bendahara_jid,
        )

    months_str = ", ".join(months_list) if months_list else "Masuk saldo simpanan"
    member_msg = (
        f"💵 *KWITANSI PEMBAYARAN TUNAI (CASH)*\n\n"
        f"Halo {member['name']},\n"
        f"Pembayaran kas tunai Anda sebesar *Rp {nominal:,}* telah diterima oleh bendahara.\n\n"
        f"📋 *Bulan Terisi:* {months_str}\n"
        f"💳 *Sisa Saldo:* Rp {alloc_result.balance_after:,}\n\n"
        f"Terima kasih! ✨"
    )
    await evo.send_text(recipient=member["phone_number"], text=member_msg)

    treasurer_msg = (
        f"✅ Pembayaran tunai #{tx_id} ({member['name']}) berhasil dicatat.\n"
        f"Nominal: Rp {nominal:,}\n"
        f"Bulan terisi: {months_str}\n"
        f"Sisa saldo: Rp {alloc_result.balance_after:,}"
    )
    await evo.send_text(recipient=settings.bendahara_jid, text=treasurer_msg)
    return {"status": "cash_recorded", "transaction_id": tx_id}


async def _handle_undo(
    db: Database,
    evo: EvolutionClient,
    quoted_id: str | None,
    settings: Any,
) -> dict[str, Any]:
    """Handle /undo rollback swipe-reply."""
    with db.connect() as conn:
        tx_repo = TransactionRepository(conn)
        tx = None
        if quoted_id:
            tx = tx_repo.get_by_notification_msg_id(quoted_id)
        if not tx:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE status = 'APPROVED' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            tx = rows[0] if rows else None

    if not tx:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="❌ Tidak ada transaksi APPROVED yang dapat di-undo.",
        )
        return {"status": "not_found"}

    with db.connect() as conn:
        result = execute_undo(conn, transaction_id=tx["id"], raise_on_error=False)

    if not result.success:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text=f"⚠️ Gagal undo transaksi #{tx['id']}: {result.message}",
        )
        return {"status": "undo_failed", "error": result.message}

    with db.connect() as conn:
        member = MemberRepository(conn).get_by_id(tx["member_id"])

    reopened_names = [
        MONTH_INDEX_TO_NAME.get(m[1], f"Bulan-{m[1]}") for m in result.reverted_months
    ]
    reopened_str = ", ".join(reopened_names) if reopened_names else "Tidak ada bulan (hanya saldo)"

    member_msg = (
        f"⚠️ *PEMBATALAN TRANSAKSI KAS*\n\n"
        f"Halo {member['name']},\n"
        f"Transaksi pembayaran kas sebesar Rp {tx['amount_paid']:,} "
        f"telah dibatalkan (/undo) oleh bendahara.\n"
        f"Saldo kas Anda dikembalikan ke: Rp {result.restored_balance:,}."
    )
    await evo.send_text(recipient=member["phone_number"], text=member_msg)

    treasurer_msg = (
        f"✅ Transaksi #{tx['id']} berhasil di-undo.\n"
        f"Saldo {member['name']} dikembalikan ke Rp {result.restored_balance:,}.\n"
        f"Bulan dibuka kembali: {reopened_str}."
    )
    await evo.send_text(recipient=settings.bendahara_jid, text=treasurer_msg)
    return {"status": "undone", "transaction_id": tx["id"]}


async def _handle_koreksi(
    db: Database,
    evo: EvolutionClient,
    text: str,
    quoted_id: str | None,
    settings: Any,
) -> dict[str, Any]:
    """Handle /koreksi <new_nominal> reallocation swipe-reply."""
    parts = text.split()
    if len(parts) < 2:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="Gunakan format: /koreksi [nominal_baru]\nContoh: /koreksi 25000",
        )
        return {"status": "syntax_error"}

    new_nominal = parse_amount(parts[1])
    if not new_nominal or new_nominal <= 0:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="Nominal koreksi tidak valid.",
        )
        return {"status": "invalid_nominal"}

    with db.connect() as conn:
        tx_repo = TransactionRepository(conn)
        tx = None
        if quoted_id:
            tx = tx_repo.get_by_notification_msg_id(quoted_id)
        if not tx:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE status = 'APPROVED' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            tx = rows[0] if rows else None

    if not tx:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="❌ Tidak ada transaksi APPROVED yang ditemukan untuk dikoreksi.",
        )
        return {"status": "not_found"}

    with db.connect() as conn:
        result = execute_koreksi(
            conn,
            transaction_id=tx["id"],
            new_amount=new_nominal,
            approved_by=settings.bendahara_jid,
            due_per_month=settings.due_amount_per_month,
            raise_on_error=False,
        )

    if not result.success:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text=f"⚠️ Gagal koreksi transaksi #{tx['id']}: {result.message}",
        )
        return {"status": "koreksi_failed", "error": result.message}

    with db.connect() as conn:
        member = MemberRepository(conn).get_by_id(tx["member_id"])

    filled_names = [
        MONTH_INDEX_TO_NAME.get(m[1], f"Bulan-{m[1]}") for m in result.new_months_filled
    ]
    filled_str = ", ".join(filled_names) if filled_names else "Masuk saldo simpanan"

    treasurer_msg = (
        f"✅ Koreksi transaksi #{tx['id']} ({member['name']}) berhasil!\n"
        f"Nominal baru: Rp {new_nominal:,}\n"
        f"Bulan terisi baru: {filled_str}\n"
        f"Sisa saldo: Rp {result.new_balance:,}"
    )
    await evo.send_text(recipient=settings.bendahara_jid, text=treasurer_msg)
    return {"status": "koreksi_success", "transaction_id": tx["id"]}


async def _handle_tambah_saldo(
    db: Database,
    evo: EvolutionClient,
    text: str,
    settings: Any,
) -> dict[str, Any]:
    """Handle /tambahsaldo <phone> <nominal>."""
    parts = text.split()
    if len(parts) < 3:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="Gunakan format: /tambahsaldo [nomor_hp] [nominal]",
        )
        return {"status": "syntax_error"}

    phone = parts[1]
    amount = parse_amount(parts[2])
    if not amount or amount <= 0:
        return {"status": "invalid_amount"}

    with db.transaction() as conn:
        member_repo = MemberRepository(conn)
        member = member_repo.get_by_phone(phone)
        if not member:
            await evo.send_text(
                recipient=settings.bendahara_jid,
                text=f"Anggota dengan nomor '{phone}' tidak ditemukan.",
            )
            return {"status": "not_found"}

        new_bal = member["balance"] + amount
        member_repo.update_balance(member["id"], new_bal)

    await evo.send_text(
        recipient=settings.bendahara_jid,
        text=f"✅ Saldo {member['name']} bertambah Rp {amount:,}. Saldo baru: Rp {new_bal:,}.",
    )
    return {"status": "balance_added", "new_balance": new_bal}


async def _handle_set_saldo(
    db: Database,
    evo: EvolutionClient,
    text: str,
    settings: Any,
) -> dict[str, Any]:
    """Handle /setsaldo <phone> <nominal>."""
    parts = text.split()
    if len(parts) < 3:
        await evo.send_text(
            recipient=settings.bendahara_jid,
            text="Gunakan format: /setsaldo [nomor_hp] [nominal]",
        )
        return {"status": "syntax_error"}

    phone = parts[1]
    amount = parse_amount(parts[2])
    if amount is None or amount < 0:
        return {"status": "invalid_amount"}

    with db.transaction() as conn:
        member_repo = MemberRepository(conn)
        member = member_repo.get_by_phone(phone)
        if not member:
            await evo.send_text(
                recipient=settings.bendahara_jid,
                text=f"Anggota dengan nomor '{phone}' tidak ditemukan.",
            )
            return {"status": "not_found"}

        member_repo.update_balance(member["id"], amount)

    await evo.send_text(
        recipient=settings.bendahara_jid,
        text=f"✅ Saldo {member['name']} berhasil diatur menjadi Rp {amount:,}.",
    )
    return {"status": "balance_set", "new_balance": amount}
