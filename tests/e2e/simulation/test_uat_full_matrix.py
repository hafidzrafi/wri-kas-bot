"""High-Fidelity Contract Simulation Test Matrix for Kas WRI Bot.

Executes all 16 UAT test scenarios defined in docs/PRODUCTION_TESTING_GUIDE.md,
validating concrete bugfixes:
- Drive upload retry on Broken pipe / transient socket failure (Case 5, B-2).
- Rejected transactions logged to Google Sheets with status REJECTED (Case 6, B-3).
- Amount paid persistence in SQLite upon treasurer manual override (Case 7, C-1).
- Month header alias matching ('December' vs 'Desember') avoiding column D (Case 7, C-1).
- Amount multiplier parsing (e.g. '30k' -> 30000).
- Pure local execution with mock Google Workspace & Evolution API.
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.handlers.receipt_handler import handle_receipt
from wri_kas_bot.handlers.treasurer_handler import handle_treasurer
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.ocr import OCRResult, ReceiptOCR
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.repository import (
    MemberRepository,
    MonthlyDuesRepository,
    TransactionRepository,
)
from wri_kas_bot.sheets import GoogleSheetsClient


def _make_text_payload(sender_jid: str, text: str, msg_id: str = "MSG_TEXT") -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-sim",
            "data": {
                "key": {"remoteJid": sender_jid, "fromMe": False, "id": msg_id},
                "message": {"conversation": text},
                "messageTimestamp": 1700000000,
            },
        }
    )


def _make_receipt_payload(
    sender_jid: str, b64_data: str, msg_id: str = "MSG_RECEIPT"
) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-sim",
            "data": {
                "key": {"remoteJid": sender_jid, "fromMe": False, "id": msg_id},
                "message": {"imageMessage": {"mimetype": "image/jpeg"}, "base64": b64_data},
                "messageTimestamp": 1700000000,
            },
        }
    )


def _make_treasurer_reply(
    text: str, quoted_id: str, msg_id: str = "MSG_TREASURER"
) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "kas-wri-sim",
            "data": {
                "key": {
                    "remoteJid": "6281100000099@s.whatsapp.net",
                    "fromMe": False,
                    "id": msg_id,
                },
                "message": {
                    "extendedTextMessage": {
                        "text": text,
                        "contextInfo": {
                            "stanzaId": quoted_id,
                            "quotedMessage": {"conversation": "Receipt notification"},
                        },
                    }
                },
                "messageTimestamp": 1700000000,
            },
        }
    )


# ---------------------------------------------------------------------------
# Skenario 1 (A-1): Cek Status Diri (/status)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_01_status_check(initialized_db):
    """A-1: Member queries /status and receives balance and dues summary."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_STATUS"}})

    payload = _make_text_payload("6281100000001@s.whatsapp.net", "/status")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    assert evo.send_text.called
    sent_text = evo.send_text.call_args.kwargs["text"]
    assert "Alice" in sent_text
    assert "Saldo:" in sent_text
    assert "Tunggakan:" in sent_text


# ---------------------------------------------------------------------------
# Skenario 2 (A-2): Informasi Pembayaran (/bayar)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_02_payment_info(initialized_db):
    """A-2: Member queries /bayar and receives QRIS/bank payment instructions."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_BAYAR"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "RESP_QRIS"}})

    payload = _make_text_payload("6281100000001@s.whatsapp.net", "/bayar")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    assert evo.send_text.called or evo.send_media.called


# ---------------------------------------------------------------------------
# Skenario 3 (A-3): Menu Bantuan (/help)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_03_help_menu(initialized_db):
    """A-3: Member sends /help or generic greeting and receives help menu."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_HELP"}})

    payload = _make_text_payload("6281100000001@s.whatsapp.net", "/help")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    assert evo.send_text.called
    sent_text = evo.send_text.call_args.kwargs["text"]
    assert "/status" in sent_text
    assert "/bayar" in sent_text


# ---------------------------------------------------------------------------
# Skenario 4 (B-1): Upload Struk Transfer & OCR
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_04_receipt_upload_ocr(initialized_db, tmp_path, monkeypatch):
    """B-1: Member uploads receipt image, local receipt is saved and card dispatched."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_RECEIPT"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_B1"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Transfer Rp 10.000"
    )

    payload = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_IMG_DATA").decode("utf-8"),
        msg_id="MSG_B1",
    )
    res = await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)

    assert res["status"] == "ok"
    tx_id = res["transaction_id"]

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx is not None
        assert tx.status == "PENDING"
        assert tx.amount_paid == 10000
        assert tx.notification_msg_id == "CARD_B1"


# ---------------------------------------------------------------------------
# Skenario 5 (B-2): Approval Normal Bendahara (/acc) + Drive Broken Pipe Retry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_05_approval_with_drive_retry(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """B-2: Treasurer approves. Drive upload throws Broken pipe first, then succeeds on retry.

    Verifies fix: Outbox worker retries Drive upload instead of proceeding with empty Drive URL.
    """
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_ACC"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_B2"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    # 1. Member submits receipt
    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_B2").decode("utf-8"),
        msg_id="MSG_B2",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    # 2. Treasurer approves
    payload_acc = _make_treasurer_reply("/acc", "CARD_B2")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    # 3. Fault Injection: Drive upload fails with Broken pipe on attempt 1, succeeds on attempt 2
    attempts = [0]
    orig_upload = mock_drive_client.upload_file

    def flaky_upload(image_bytes: bytes, filename: str) -> str:
        attempts[0] += 1
        if attempts[0] == 1:
            raise BrokenPipeError("[Errno 32] Broken pipe")
        return orig_upload(image_bytes, filename)

    mock_drive_client.upload_file = flaky_upload

    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    processed = await worker.run_outbox_cycle()

    assert processed == 1
    assert attempts[0] == 2  # Proves retry happened!

    # Verify Google Sheets got valid Drive URL, NOT empty string!
    last_log = mock_sheets_client.appended_rows[-1]
    assert last_log["Status"] == "APPROVED"
    assert "drive.google.com" in last_log["Bukti URL"]
    assert last_log["Nominal"] == 10000

    # Verify SQLite updated
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        assert tx.sync_status == "SYNCED"
        assert "drive.google.com" in tx.proof_drive_url


# ---------------------------------------------------------------------------
# Skenario 6 (B-3): Penolakan Bukti Transfer (/reject) & Log Sync
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_06_rejection_logged_to_sheets(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """B-3: Treasurer rejects blurry receipt.

    Verifies fix: Outbox worker syncs rejected transaction to log_transaksi with status REJECTED.
    """
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_REJ"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_B3"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(nominal=None, bank=None, raw_text="")

    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"BLURRY_B3").decode("utf-8"),
        msg_id="MSG_B3",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    # Treasurer rejects with custom reason
    payload_rej = _make_treasurer_reply("/reject Foto buram, silakan kirim ulang", "CARD_B3")
    res_rej = await handle_treasurer(payload_rej, db=db, evolution=evo)
    assert res_rej["status"] == "rejected"

    # Outbox worker executes cycle
    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    processed = await worker.run_outbox_cycle()
    assert processed == 1

    # Verify row appended to Sheets log_transaksi
    last_log = mock_sheets_client.appended_rows[-1]
    assert last_log["Status"] == "REJECTED"
    assert last_log["Nominal"] == 0
    assert "Ditolak: Foto buram" in last_log["Keterangan"]

    # Verify no dues cells were touched
    assert len(mock_sheets_client.updated_cells) == 0

    # Verify SQLite transaction record
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REJECTED"
        assert tx.sync_status == "SYNCED"


# ---------------------------------------------------------------------------
# Skenario 7 (C-1): Alokasi Multi-Bulan (Rp 30.000) & /acc 30k Override & Month Alias
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_07_multi_month_acc_30k_and_december_alias(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """C-1: Member uploads receipt with failed OCR. Treasurer overrides with /acc 30k.

    Verifies:
    1. '30k' multiplier parsed to 30000.
    2. amount_paid persisted as 30000 in SQLite transactions table (Case 7 fix).
    3. 3 months allocated (Februari, Maret, April for fresh member).
    4. Sheets log_transaksi records Nominal: 30000, NOT 0.
    5. December month alias matching contract verification.
    """
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_C1"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_C1"}})

    # OCR returns None (blank)
    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(nominal=None, bank=None, raw_text="")

    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_C1").decode("utf-8"),
        msg_id="MSG_C1",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    # Treasurer overrides with /acc 30k
    payload_acc = _make_treasurer_reply("/acc 30k", "CARD_C1")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    # Verify amount_paid persisted in SQLite as 30000!
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.amount_paid == 30000
        assert len(tx.months_filled) == 3

    # Outbox sync
    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    processed = await worker.run_outbox_cycle()
    assert processed == 1

    last_log = mock_sheets_client.appended_rows[-1]
    assert last_log["Nominal"] == 30000  # NOT 0!
    assert last_log["Status"] == "APPROVED"


def test_uat_scenario_07_december_column_alias_contract():
    """Verify December alias in GoogleSheetsClient maps to column 12 (L), never D (April)."""
    client = GoogleSheetsClient()
    client._get_values = MagicMock(
        return_value=[
            [
                "Nama",
                "Divisi",
                "Jabatan",
                "April",
                "Mei",
                "Juni",
                "Juli",
                "Agustus",
                "September",
                "Oktober",
                "November",
                "December",
            ],
            ["Alice", "Public Relations", "Staff", "", "", "", "", "", "", "", "", ""],
        ]
    )
    mock_service = MagicMock()
    client._service = mock_service

    coord = client.update_payment_month(name="Alice", month="Desember", proof_url="Bank Jago")
    # Col index 12 corresponds to column L, row 2
    assert coord == "PENGURUS-2025/2026!L2"
    assert not coord.startswith("PENGURUS-2025/2026!D")


# ---------------------------------------------------------------------------
# Skenario 8 (C-2): Alokasi Parsial / Sisa Saldo (Contoh: Rp 15.000)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_08_partial_payment(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """C-2: Member transfers Rp 15.000 (1 month + Rp 5.000 deposit)."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_C2"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_C2"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=15000, bank="Bank Jago", raw_text="Rp 15.000"
    )

    payload_receipt = _make_receipt_payload(
        "6281100000002@s.whatsapp.net",  # Bob
        base64.b64encode(b"RECEIPT_C2").decode("utf-8"),
        msg_id="MSG_C2",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    # Treasurer approves
    payload_acc = _make_treasurer_reply("/acc", "CARD_C2")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.amount_paid == 15000
        assert tx.balance_after == 5000
        assert len(tx.months_filled) == 1

        bob = MemberRepository(conn).get_by_phone("6281100000002")
        assert bob.balance == 5000

    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    await worker.run_outbox_cycle()

    last_log = mock_sheets_client.appended_rows[-1]
    assert last_log["Nominal"] == 15000
    assert last_log["Sisa Saldo"] == 5000


# ---------------------------------------------------------------------------
# Skenario 9 (C-3): Akumulasi Deposit Menutup Bulan (Rp 5.000 + Saldo Rp 5.000)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_09_deposit_accumulation(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """C-3: Member with Rp 5.000 balance transfers Rp 5.000 to close 1 month."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_C3"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_C3"}})

    # Seed Charlie with Rp 5.000 balance
    with db.transaction() as conn:
        charlie = MemberRepository(conn).get_by_phone("6281100000003")
        MemberRepository(conn).update_balance(charlie.id, 5000)

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=5000, bank="Bank Jago", raw_text="Rp 5.000"
    )

    payload_receipt = _make_receipt_payload(
        "6281100000003@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_C3").decode("utf-8"),
        msg_id="MSG_C3",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    payload_acc = _make_treasurer_reply("/acc", "CARD_C3")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.balance_before == 5000
        assert tx.balance_after == 0
        assert len(tx.months_filled) == 1

        charlie = MemberRepository(conn).get_by_phone("6281100000003")
        assert charlie.balance == 0


# ---------------------------------------------------------------------------
# Skenario 10 (C-4): Pembayaran di Muka (Advance Payment)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_10_advance_payment(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """C-4: Member pays Rp 20.000 advance payment for future months."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_C4"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_C4"}})

    # Mark first 9 months as already paid for Alice
    with db.transaction() as conn:
        alice = MemberRepository(conn).get_by_phone("6281100000001")
        dues = MonthlyDuesRepository(conn).get_unpaid(alice.id, year=2026)
        for d in dues[:9]:
            MonthlyDuesRepository(conn).mark_paid(d.id, transaction_id=None)

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=20000, bank="Bank Jago", raw_text="Rp 20.000"
    )

    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_C4").decode("utf-8"),
        msg_id="MSG_C4",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    assert res_receipt["transaction_id"] > 0

    payload_acc = _make_treasurer_reply("/acc", "CARD_C4")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    with db.connect() as conn:
        unpaid = MonthlyDuesRepository(conn).get_unpaid(alice.id, year=2026)
        # 11 initial - 9 prior - 2 advance = 0 unpaid
        assert len(unpaid) == 0


# ---------------------------------------------------------------------------
# Skenario 11 (C-5): Nominal Kurang dari 1 Bulan (Rp 5.000 Masuk Saldo)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_11_underpayment_to_deposit(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """C-5: Member pays Rp 5.000 with 0 starting balance -> 0 months, saldo Rp 5.000."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_C5"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_C5"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=5000, bank="Bank Jago", raw_text="Rp 5.000"
    )

    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_C5").decode("utf-8"),
        msg_id="MSG_C5",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    payload_acc = _make_treasurer_reply("/acc", "CARD_C5")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.months_filled == []
        assert tx.balance_after == 5000


# ---------------------------------------------------------------------------
# Skenario 12 (D-1): Override Manual Nominal (/acc 20000)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_12_treasurer_nominal_override(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """D-1: OCR reads 10000, treasurer overrides to 20000."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_D1"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_D1"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_D1").decode("utf-8"),
        msg_id="MSG_D1",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]

    # Treasurer overrides with /acc 20000
    payload_acc = _make_treasurer_reply("/acc 20000", "CARD_D1")
    res_acc = await handle_treasurer(payload_acc, db=db, evolution=evo)
    assert res_acc["status"] == "approved"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.amount_paid == 20000
        assert len(tx.months_filled) == 2


# ---------------------------------------------------------------------------
# Skenario 13 (E-1 & E-2): Pembayaran Kas Tunai (/tunai)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_13_cash_payment_flow(
    initialized_db, mock_drive_client, mock_sheets_client
):
    """E-1 & E-2: Treasurer records cash payment via /tunai 6281100000001 25000."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_CASH"}})

    payload = _make_text_payload(
        "6281100000099@s.whatsapp.net",
        "/tunai 6281100000001 25000",
        msg_id="CMD_CASH",
    )
    res = await handle_treasurer(payload, db=db, evolution=evo)
    assert res["status"] == "cash_recorded"

    tx_id = res["transaction_id"]
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.payment_method == "CASH"
        assert tx.amount_paid == 25000
        assert tx.balance_after == 5000
        assert len(tx.months_filled) == 2

    # Outbox sync
    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    processed = await worker.run_outbox_cycle()
    assert processed == 1

    # Cash must NOT upload to Drive
    assert len(mock_drive_client.uploaded_files) == 0

    # Sheets matrix must contain 'Tunai'
    assert mock_sheets_client.updated_cells[-1]["proof_url"] == "Tunai"

    # Sheets log must contain 'Tunai'
    last_log = mock_sheets_client.appended_rows[-1]
    assert last_log["Bukti URL"] == "Tunai"
    assert last_log["Nominal"] == 25000


# ---------------------------------------------------------------------------
# Skenario 14 (F-1): Pembatalan Transaksi (/undo)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_14_undo_flow(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """F-1: Treasurer executes /undo on previous transaction.

    Verifies dues restored to UNPAID, balance reverted, and Sheets batch_clear executed.
    """
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_UNDO"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_F1"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    # 1. Approve a transaction
    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_F1").decode("utf-8"),
        msg_id="MSG_F1",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = res_receipt["transaction_id"]
    await handle_treasurer(_make_treasurer_reply("/acc", "CARD_F1"), db=db, evolution=evo)

    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    await worker.run_outbox_cycle()

    # 2. Treasurer executes /undo
    payload_undo = _make_text_payload(
        "6281100000099@s.whatsapp.net", f"/undo {tx_id}", msg_id="CMD_UNDO"
    )
    res_undo = await handle_treasurer(payload_undo, db=db, evolution=evo)
    assert res_undo["status"] == "undone"

    # 3. Outbox reverse sync
    await worker.run_outbox_cycle()

    # Verify batch clear was called
    assert len(mock_sheets_client.cleared_ranges) > 0

    # Verify log_transaksi recorded negative nominal and REVERTED status
    last_log = mock_sheets_client.appended_rows[-1]
    assert last_log["Status"] == "REVERTED"
    assert last_log["Nominal"] == -10000


# ---------------------------------------------------------------------------
# Skenario 15 (F-2): Koreksi Transaksi (/koreksi)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_15_correction_flow(
    initialized_db, mock_drive_client, mock_sheets_client, tmp_path, monkeypatch
):
    """F-2: Treasurer executes /koreksi 20000 to change previous transaction nominal."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_KOR"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_F2"}})

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    payload_receipt = _make_receipt_payload(
        "6281100000001@s.whatsapp.net",
        base64.b64encode(b"RECEIPT_F2").decode("utf-8"),
        msg_id="MSG_F2",
    )
    res_receipt = await handle_receipt(payload_receipt, db=db, evolution=evo, ocr_engine=mock_ocr)
    assert res_receipt["transaction_id"] > 0
    await handle_treasurer(_make_treasurer_reply("/acc", "CARD_F2"), db=db, evolution=evo)

    worker = OutboxWorker(db=db, drive_client=mock_drive_client, sheets_client=mock_sheets_client)
    await worker.run_outbox_cycle()

    # Treasurer executes /koreksi
    payload_kor = _make_treasurer_reply("/koreksi 20000", "CARD_F2")
    res_kor = await handle_treasurer(payload_kor, db=db, evolution=evo)
    assert res_kor["status"] == "koreksi_success"

    await worker.run_outbox_cycle()

    with db.connect() as conn:
        alice = MemberRepository(conn).get_by_phone("6281100000001")
        dues = MonthlyDuesRepository(conn).get_paid(alice.id, year=2026)
        assert len(dues) == 2


# ---------------------------------------------------------------------------
# Skenario 16 (G-1, G-2, G-3): Keamanan & Batasan Hak Akses
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uat_scenario_16_security_and_rapid_ingestion(initialized_db, tmp_path, monkeypatch):
    """G-1, G-2, G-3: Security boundaries & race condition protection.

    - G-1: Unregistered number rejected politely.
    - G-2: Unauthorized non-treasurer command rejected.
    - G-3: Concurrent receipt ingestion processed without deadlock.
    """
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_SEC"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_SEC"}})

    # G-1: Unregistered number
    payload_stranger = _make_text_payload("6289999999999@s.whatsapp.net", "/status")
    res_stranger = await handle_member(payload_stranger, db=db, evolution=evo)
    assert res_stranger["status"] == "unregistered"

    # G-2: Unauthorized member sends /acc
    payload_unauth = _make_text_payload("6281100000001@s.whatsapp.net", "/acc")
    res_unauth = await handle_treasurer(payload_unauth, db=db, evolution=evo)
    assert res_unauth["status"] == "unauthorized"

    # G-3: Concurrent submissions (3 receipts submitted in parallel)
    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=10000, bank="Bank Jago", raw_text="Rp 10.000"
    )

    async def _submit(i: int):
        payload = _make_receipt_payload(
            "6281100000001@s.whatsapp.net",
            base64.b64encode(f"IMG_{i}".encode()).decode(),
            msg_id=f"MSG_CONC_{i}",
        )
        return await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)

    results = await asyncio.gather(_submit(1), _submit(2), _submit(3))
    assert all(r["status"] == "ok" for r in results)

    with db.connect() as conn:
        txs = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE member_id = 1 AND status = 'PENDING'"
        ).fetchone()[0]
        assert txs == 3
