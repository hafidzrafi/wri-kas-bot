"""End-to-end simulation tests for bot operations and edge cases."""

import base64
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from wri_kas_bot.app import app
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
from wri_kas_bot.sync import sync_members_from_sheets


# Feature 1: Google Sheets Ingestion & Synchronization (Februari - Desember)
@pytest.mark.asyncio
async def test_sim_01_sheets_sync_and_member_ingestion(tmp_db_path, sqlite_schema_sql):
    """Simulate initial sync of 40 members from Google Sheets to empty SQLite."""
    import sqlite3

    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(sqlite_schema_sql)
    conn.close()

    db = Database(tmp_db_path)

    # 40 synthetic members
    mock_members = [
        {
            "name": f"Pengurus_{i:02d}",
            "phone_number": f"62811000000{i:02d}",
            "status": "Aktif",
            "deposit_balance": 0,
        }
        for i in range(1, 41)
    ]

    mock_sheets = MagicMock()
    mock_sheets.fetch_all_members.return_value = mock_members

    synced_count = sync_members_from_sheets(db, sheets_client=mock_sheets, year=2026)
    assert synced_count == 40

    with db.connect() as conn:
        m_repo = MemberRepository(conn)
        d_repo = MonthlyDuesRepository(conn)

        active = m_repo.list_active()
        assert len(active) == 40

        # Verify each member has 11 dues initialized starting in February
        for m in active:
            unpaid = d_repo.get_unpaid(m.id, year=2026)
            assert len(unpaid) == 11
            assert unpaid[0].month == "Februari"
            assert unpaid[0].month_index == 2
            assert unpaid[-1].month == "Desember"
            assert unpaid[-1].month_index == 12


# Feature 2: WhatsApp UX Blue Ticks & Typing Presence
@pytest.mark.asyncio
async def test_sim_02_whatsapp_blue_ticks_and_typing_presence(
    initialized_db,
    mock_evolution_api,
    make_webhook_payload,
    monkeypatch,
):
    """Verify inbound WhatsApp messages trigger markMessageAsRead and sendPresence."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{initialized_db}")
    monkeypatch.setenv("TYPING_DELAY_ENABLED", "true")
    monkeypatch.setenv("TYPING_DELAY_BASE_MS", "100")  # Fast for test execution

    # Intercept markMessageAsRead and sendPresence calls
    read_calls = []
    presence_calls = []

    mock_evolution_api.post(url__regex=r"/chat/markMessageAsRead/.*").mock(
        side_effect=lambda request: (
            read_calls.append(request.read().decode()),
            httpx.Response(200, json={"status": "success"}),
        )[1]
    )
    mock_evolution_api.post(url__regex=r"/chat/sendPresence/.*").mock(
        side_effect=lambda request: (
            presence_calls.append(request.read().decode()),
            httpx.Response(200, json={"status": "success"}),
        )[1]
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(
            sender_phone="6281100000001",
            message_text="/status",
            message_id="MSG_WA_SIM_001",
        )
        resp = await client.post("/webhook", json=payload)
        assert resp.status_code == 200

    assert len(read_calls) == 1
    assert "MSG_WA_SIM_001" in read_calls[0]
    assert "6281100000001@s.whatsapp.net" in read_calls[0]

    assert len(presence_calls) == 1
    assert "composing" in presence_calls[0]
    assert "6281100000001@s.whatsapp.net" in presence_calls[0]


# Feature 3: Unregistered Member & Auto-Sync Fallback
@pytest.mark.asyncio
async def test_sim_03_unregistered_member_and_fallback_sync(
    initialized_db,
    mock_evolution_api,
    make_webhook_payload,
):
    """Verify unknown member receives polite notice, but gets auto-registered if in Sheets."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "NOTICE_1"}})

    # Case A: Totally unregistered user
    unregistered_payload = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6289999999999", message_text="/status")
    )
    res_a = await handle_member(unregistered_payload, db=db, evolution=evo)
    assert res_a["status"] == "unregistered"
    assert evo.send_text.called
    sent_text = evo.send_text.call_args[1]["text"]
    assert "belum terdaftar sebagai anggota/pengurus aktif WRI" in sent_text

    # Case B: Member newly added to Sheets -> sync on demand
    new_member_phone = "6281234567890"
    mock_sheets = MagicMock()
    mock_sheets.fetch_all_members.return_value = [
        {"name": "David", "phone_number": new_member_phone, "status": "Aktif", "deposit_balance": 0}
    ]
    sync_members_from_sheets(db, sheets_client=mock_sheets, year=2026)

    # Now David queries /status
    david_payload = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone=new_member_phone, message_text="/status")
    )
    res_b = await handle_member(david_payload, db=db, evolution=evo)
    assert res_b["status"] == "ok"
    assert res_b["unpaid_count"] == 11


# Feature 4: Member Self-Service: /help & /bantuan
@pytest.mark.asyncio
async def test_sim_04_member_help_command(
    initialized_db,
    make_webhook_payload,
):
    """Verify /help command returns guide of available bot commands."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "HELP_1"}})

    payload = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000001", message_text="/help")
    )
    res = await handle_member(payload, db=db, evolution=evo)
    assert res["status"] == "ok"
    assert evo.send_text.called
    body = evo.send_text.call_args[1]["text"]
    assert "/status" in body
    assert "/bayar" in body
    assert "Upload bukti transfer" in body

    # Greetings and unrecognized text fallback
    evo.send_text.reset_mock()
    greeting_payload = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000001", message_text="Hai")
    )
    res_greet = await handle_member(greeting_payload, db=db, evolution=evo)
    assert res_greet["status"] == "ok"
    assert evo.send_text.called
    assert "/status" in evo.send_text.call_args[1]["text"]


# Feature 5: Member Self-Service: /bayar
@pytest.mark.asyncio
async def test_sim_05_member_bayar_command(
    initialized_db,
    make_webhook_payload,
):
    """Verify /bayar command returns official bank account details and QRIS."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "BAYAR_1"}})

    payload = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000001", message_text="/bayar")
    )
    res = await handle_member(payload, db=db, evolution=evo)
    assert res["status"] == "ok"
    assert evo.send_text.called
    body = evo.send_text.call_args[1]["text"]
    assert "Bank Jago" in body
    assert "QRIS" in body


# Feature 6: Member Self-Service: /status
@pytest.mark.asyncio
async def test_sim_06_member_status_command(
    initialized_db,
    make_webhook_payload,
):
    """Verify /status displays accurate unpaid list (Februari-Desember) and balance."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "STATUS_1"}})

    payload = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000001", message_text="/status")
    )
    res = await handle_member(payload, db=db, evolution=evo)
    assert res["status"] == "ok"
    assert res["unpaid_count"] == 11
    assert res["net_bill"] == 110000
    assert evo.send_text.called
    body = evo.send_text.call_args[1]["text"]
    assert "Februari" in body
    assert "Desember" in body


# Feature 7 & 8: Receipt Ingestion, Gemini OCR & Treasurer Notification Card
@pytest.mark.asyncio
async def test_sim_07_and_08_receipt_ingest_ocr_and_forwarding(
    initialized_db,
    make_webhook_payload,
    tmp_path,
    monkeypatch,
):
    """Verify receipt image ingestion, OCR extraction, and treasurer card dispatch."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    b64_img = base64.b64encode(b"RECEIPT_SIM_20K").decode("utf-8")
    payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000001",
            image_base64=b64_img,
            message_text="Bayar kas 2 bulan",
        )
    )

    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(
        nominal=20000, bank="Bank Jago", raw_text="Transfer Bank Jago Rp 20.000"
    )

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "ACK_RECEIPT_1"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "TREASURER_CARD_1"}})

    res = await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)
    assert res["status"] == "ok"
    tx_id = res["transaction_id"]

    # Verify transaction stored in SQLite as PENDING
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx is not None
        assert tx.status == "PENDING"
        assert tx.ocr_amount == 20000
        assert tx.ocr_bank == "Bank Jago"

    # Verify card dispatched to treasurer
    assert evo.send_media.called
    card_caption = evo.send_media.call_args[1]["caption"]
    assert "BUKTI KAS MASUK" in card_caption
    assert "20,000" in card_caption or "20.000" in card_caption
    assert "/acc" in card_caption


# Feature 9: Treasurer /acc Approval with FIFO Allocation
@pytest.mark.asyncio
async def test_sim_09_treasurer_acc_approval_fifo(
    initialized_db,
    make_webhook_payload,
    tmp_path,
    monkeypatch,
):
    """Verify treasurer /acc transitions transaction to APPROVED and allocates dues."""
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(initialized_db)

    # 1. Ingest Rp 20,000 receipt
    payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000001",
            image_base64=base64.b64encode(b"IMG").decode("utf-8"),
        )
    )
    mock_ocr = AsyncMock(spec=ReceiptOCR)
    mock_ocr.extract_receipt.return_value = OCRResult(nominal=20000, bank="BCA")

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "MEMBER_ACK"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "CARD_STANZA_9"}})

    ingest_res = await handle_receipt(payload, db=db, evolution=evo, ocr_engine=mock_ocr)
    tx_id = ingest_res["transaction_id"]

    # 2. Treasurer approves
    acc_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text=f"/acc {tx_id}",
        )
    )
    acc_res = await handle_treasurer(acc_payload, db=db, evolution=evo)
    assert acc_res["status"] == "approved"
    assert acc_res["transaction_id"] == tx_id

    # 3. Verify SQLite state: Februari and Maret are PAID
    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "APPROVED"
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 2
        assert [d.month for d in paid_dues] == ["Februari", "Maret"]

        # Transaction marked for outbox sync
        assert tx.sync_status == "PENDING"


# Feature 10: Idempotency Edge Case: Duplicate /acc Rejection
@pytest.mark.asyncio
async def test_sim_10_edge_case_duplicate_acc_idempotency(
    initialized_db,
    make_webhook_payload,
):
    """Verify duplicate /acc attempts on already-approved transactions fail safely."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "1"}})

    # Create an APPROVED transaction
    with db.transaction() as conn:
        tx_id = TransactionRepository(conn).create_transaction(
            member_id=1,
            amount_paid=10000,
            payment_method="TRANSFER_JAGO",
            status="APPROVED",
        )

    # Attempt second /acc
    dup_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text=f"/acc {tx_id}",
        )
    )
    res = await handle_treasurer(dup_payload, db=db, evolution=evo)
    assert res["status"] == "error"
    assert "sudah diproses" in res["detail"]


# Feature 11 & 12: Treasurer /reject & Edge Case Duplicate Rejection
@pytest.mark.asyncio
async def test_sim_11_and_12_treasurer_reject_and_idempotency(
    initialized_db,
    make_webhook_payload,
):
    """Verify /reject sets REJECTED status, notifies member, and rejects duplicate actions."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "1"}})

    # Create PENDING transaction
    with db.transaction() as conn:
        tx_id = TransactionRepository(conn).create_transaction(
            member_id=1,
            amount_paid=10000,
            payment_method="TRANSFER_JAGO",
            status="PENDING",
            ocr_amount=10000,
        )

    # 1. Reject with custom audit reason
    reject_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text=f"/reject {tx_id} Bukti transfer buram",
        )
    )
    rej_res = await handle_treasurer(reject_payload, db=db, evolution=evo)
    assert rej_res["status"] == "rejected"
    assert rej_res["reason"] == "Bukti transfer buram"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REJECTED"

    # Member notified with reason
    member_notif = [
        call[1]["text"]
        for call in evo.send_text.call_args_list
        if "ditolak" in call[1]["text"].lower()
    ]
    assert len(member_notif) >= 1
    assert "Bukti transfer buram" in member_notif[0]

    # 2. Duplicate /reject attempt
    dup_res = await handle_treasurer(reject_payload, db=db, evolution=evo)
    assert dup_res["status"] == "error"
    assert "sudah diproses" in dup_res["detail"]


# Feature 13: Treasurer /tunai Cash Payment
@pytest.mark.asyncio
async def test_sim_13_treasurer_tunai_cash_payment(
    initialized_db,
    make_webhook_payload,
):
    """Verify /tunai command records manual cash payment and allocates dues."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "CASH_1"}})

    tunai_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/tunai 6281100000001 20000",
        )
    )
    res = await handle_treasurer(tunai_payload, db=db, evolution=evo)
    assert res["status"] == "cash_recorded"

    with db.connect() as conn:
        paid_dues = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid_dues) == 2
        assert [d.month for d in paid_dues] == ["Februari", "Maret"]

        tx = TransactionRepository(conn).get_by_id(res["transaction_id"])
        assert tx.sync_status == "PENDING"


# Feature 14 & 15: Treasurer /undo Rollback & Nonexistent Edge Case
@pytest.mark.asyncio
async def test_sim_14_and_15_treasurer_undo_and_edge_cases(
    initialized_db,
    make_webhook_payload,
):
    """Verify /undo reverts dues to UNPAID, restores balance, and queues batchClear."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "UNDO_1"}})

    # Record Rp 10,000 cash payment
    tunai_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/tunai 6281100000001 10000",
        )
    )
    tunai_res = await handle_treasurer(tunai_payload, db=db, evolution=evo)
    tx_id = tunai_res["transaction_id"]

    # Verify Februari is PAID
    with db.connect() as conn:
        paid = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid) == 1

    # 1. Successful /undo
    undo_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text=f"/undo {tx_id}",
        )
    )
    undo_res = await handle_treasurer(undo_payload, db=db, evolution=evo)
    assert undo_res["status"] == "undone"

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.status == "REVERTED"
        # Februari reverted back to UNPAID
        paid = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid) == 0

        # Sync status updated
        assert tx.sync_status in ("PENDING", "REVERTED")

    # 2. Edge Case: /undo on invalid ID
    invalid_undo_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/undo 99999",
        )
    )
    inv_res = await handle_treasurer(invalid_undo_payload, db=db, evolution=evo)
    assert inv_res["status"] == "error"
    assert "tidak ditemukan" in inv_res["detail"]


# Feature 16: Treasurer /koreksi Amount Revision
@pytest.mark.asyncio
async def test_sim_16_treasurer_koreksi_amount_revision(
    initialized_db,
    make_webhook_payload,
):
    """Verify /koreksi rolls back old allocation and applies new nominal via FIFO."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "1"}})

    # Record initial payment of Rp 10,000 (Februari paid)
    tunai_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/tunai 6281100000001 10000",
        )
    )
    t_res = await handle_treasurer(tunai_payload, db=db, evolution=evo)
    tx_id = t_res["transaction_id"]

    # Treasurer corrects amount to Rp 30,000
    koreksi_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text=f"/koreksi {tx_id} 30000",
        )
    )
    k_res = await handle_treasurer(koreksi_payload, db=db, evolution=evo)
    assert k_res["status"] == "koreksi_success"
    assert k_res["transaction_id"] == tx_id

    with db.connect() as conn:
        tx = TransactionRepository(conn).get_by_id(tx_id)
        assert tx.amount_paid == 30000
        paid = MonthlyDuesRepository(conn).get_paid(member_id=1, year=2026)
        assert len(paid) == 3
        assert [d.month for d in paid] == ["Februari", "Maret", "April"]


# Feature 17 & 18: Treasurer /tambahsaldo and /setsaldo
@pytest.mark.asyncio
async def test_sim_17_and_18_treasurer_balance_adjustments(
    initialized_db,
    make_webhook_payload,
):
    """Verify manual balance increment (/tambahsaldo) and override (/setsaldo)."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "1"}})

    # 1. /tambahsaldo 5000
    tambah_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/tambahsaldo 6281100000001 5000",
        )
    )
    t_res = await handle_treasurer(tambah_payload, db=db, evolution=evo)
    assert t_res["status"] == "balance_added"
    assert t_res["new_balance"] == 5000

    with db.connect() as conn:
        m = MemberRepository(conn).get_by_id(1)
        assert m.balance == 5000

    # 2. /setsaldo 15000
    set_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/setsaldo 6281100000001 15000",
        )
    )
    s_res = await handle_treasurer(set_payload, db=db, evolution=evo)
    assert s_res["status"] == "balance_set"
    assert s_res["new_balance"] == 15000

    with db.connect() as conn:
        m = MemberRepository(conn).get_by_id(1)
        assert m.balance == 15000


# Feature 19: Outbox Worker & Google Drive/Sheets Synchronization
@pytest.mark.asyncio
async def test_sim_19_outbox_worker_drive_and_sheets_sync(
    initialized_db,
    mock_drive_client,
    mock_sheets_client,
    make_webhook_payload,
):
    """Verify outbox worker executes pending tasks and calls Drive & Sheets."""
    db = Database(initialized_db)
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "1"}})

    # Record cash payment of Rp 20,000 (Februari & Maret)
    tunai_payload = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/tunai 6281100000001 20000",
        )
    )
    await handle_treasurer(tunai_payload, db=db, evolution=evo)

    # Process outbox queue
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 1

    # Verify Google Sheets cell updates
    assert len(mock_sheets_client.updated_cells) == 2
    assert [c["month"] for c in mock_sheets_client.updated_cells] == ["Februari", "Maret"]

    with db.connect() as conn:
        rows = conn.execute("SELECT id FROM transactions WHERE sync_status = 'PENDING'").fetchall()
        assert len(rows) == 0


# Feature 20: Full Multi-Member Semester Lifecycle Simulation
@pytest.mark.asyncio
async def test_sim_20_comprehensive_semester_lifecycle_simulation(
    tmp_db_path,
    sqlite_schema_sql,
    mock_drive_client,
    mock_sheets_client,
    make_webhook_payload,
    tmp_path,
    monkeypatch,
):
    """End-to-end semester lifecycle simulation testing 5 concurrent members."""
    import sqlite3

    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(sqlite_schema_sql)
    conn.close()

    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path / "receipts"))
    db = Database(tmp_db_path)

    members_data = [
        {"name": "Alice", "phone_number": "6281100000001", "status": "Aktif", "deposit_balance": 0},
        {"name": "Bob", "phone_number": "6281100000002", "status": "Aktif", "deposit_balance": 0},
        {
            "name": "Charlie",
            "phone_number": "6281100000003",
            "status": "Aktif",
            "deposit_balance": 0,
        },
        {"name": "David", "phone_number": "6281100000004", "status": "Aktif", "deposit_balance": 0},
        {"name": "Eva", "phone_number": "6281100000005", "status": "Aktif", "deposit_balance": 0},
    ]
    mock_sync_client = MagicMock()
    mock_sync_client.fetch_all_members.return_value = members_data
    synced = sync_members_from_sheets(db, sheets_client=mock_sync_client, year=2026)
    assert synced == 5

    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "SIM_TEXT"}})
    evo.send_media = AsyncMock(return_value={"key": {"id": "SIM_MEDIA"}})

    # 2. Alice pays 1 month (Rp 10,000)
    ocr_10k = AsyncMock(spec=ReceiptOCR)
    ocr_10k.extract_receipt.return_value = OCRResult(nominal=10000, bank="BCA")
    payload_alice = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000001",
            image_base64=base64.b64encode(b"ALICE").decode("utf-8"),
        )
    )
    alice_res = await handle_receipt(payload_alice, db=db, evolution=evo, ocr_engine=ocr_10k)
    tx_alice = alice_res["transaction_id"]

    # Treasurer approves Alice
    acc_alice = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000099", message_text=f"/acc {tx_alice}")
    )
    await handle_treasurer(acc_alice, db=db, evolution=evo)

    # 3. Bob pays 3 months in advance (Rp 30,000)
    ocr_30k = AsyncMock(spec=ReceiptOCR)
    ocr_30k.extract_receipt.return_value = OCRResult(nominal=30000, bank="Mandiri")
    payload_bob = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000002",
            image_base64=base64.b64encode(b"BOB").decode("utf-8"),
        )
    )
    bob_res = await handle_receipt(payload_bob, db=db, evolution=evo, ocr_engine=ocr_30k)
    tx_bob = bob_res["transaction_id"]

    # Treasurer approves Bob
    acc_bob = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000099", message_text=f"/acc {tx_bob}")
    )
    await handle_treasurer(acc_bob, db=db, evolution=evo)

    # 4. Charlie pays partial / odd nominal (Rp 5,000)
    ocr_5k = AsyncMock(spec=ReceiptOCR)
    ocr_5k.extract_receipt.return_value = OCRResult(nominal=5000, bank="BRI")
    payload_charlie = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000003",
            image_base64=base64.b64encode(b"CHARLIE").decode("utf-8"),
        )
    )
    charlie_res = await handle_receipt(payload_charlie, db=db, evolution=evo, ocr_engine=ocr_5k)
    tx_charlie = charlie_res["transaction_id"]

    # Treasurer approves Charlie
    acc_charlie = WebhookPayload.model_validate(
        make_webhook_payload(sender_phone="6281100000099", message_text=f"/acc {tx_charlie}")
    )
    await handle_treasurer(acc_charlie, db=db, evolution=evo)

    # 5. David uploads blurred receipt -> Treasurer rejects
    ocr_invalid = AsyncMock(spec=ReceiptOCR)
    ocr_invalid.extract_receipt.return_value = OCRResult(nominal=10000, bank="UNKNOWN")
    payload_david = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000004",
            image_base64=base64.b64encode(b"DAVID").decode("utf-8"),
        )
    )
    david_res = await handle_receipt(payload_david, db=db, evolution=evo, ocr_engine=ocr_invalid)
    tx_david = david_res["transaction_id"]

    reject_david = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text=f"/reject {tx_david} Bukti buram, mohon upload ulang",
        )
    )
    await handle_treasurer(reject_david, db=db, evolution=evo)

    # 6. Eva pays cash directly to treasurer (Rp 20,000)
    tunai_eva = WebhookPayload.model_validate(
        make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/tunai 6281100000005 20000",
        )
    )
    await handle_treasurer(tunai_eva, db=db, evolution=evo)

    # 7. Outbox worker processes all queued jobs
    worker = OutboxWorker(db=db, sheets_client=mock_sheets_client, drive_client=mock_drive_client)
    processed = await worker.run_outbox_cycle()
    assert processed >= 4

    # 8. Verify entire ledger integrity
    with db.connect() as conn:
        m_repo = MemberRepository(conn)
        d_repo = MonthlyDuesRepository(conn)

        # Alice: 1 month paid (Februari), balance 0, 10 unpaid
        alice = m_repo.get_by_id(1)
        assert alice.balance == 0
        assert len(d_repo.get_paid(1, 2026)) == 1
        assert len(d_repo.get_unpaid(1, 2026)) == 10

        # Bob: 3 months paid (Feb, Mar, Apr), balance 0, 8 unpaid
        bob = m_repo.get_by_id(2)
        assert bob.balance == 0
        assert len(d_repo.get_paid(2, 2026)) == 3
        assert len(d_repo.get_unpaid(2, 2026)) == 8

        # Charlie: 0 months paid, balance Rp 5,000, 11 unpaid
        charlie = m_repo.get_by_id(3)
        assert charlie.balance == 5000
        assert len(d_repo.get_paid(3, 2026)) == 0
        assert len(d_repo.get_unpaid(3, 2026)) == 11

        # David: 0 months paid, balance 0, 11 unpaid (rejected)
        david = m_repo.get_by_id(4)
        assert david.balance == 0
        assert len(d_repo.get_paid(4, 2026)) == 0
        assert len(d_repo.get_unpaid(4, 2026)) == 11

        # Eva: 2 months paid (Feb, Mar), balance 0, 9 unpaid (cash)
        eva = m_repo.get_by_id(5)
        assert eva.balance == 0
        assert len(d_repo.get_paid(5, 2026)) == 2
        assert len(d_repo.get_unpaid(5, 2026)) == 9
