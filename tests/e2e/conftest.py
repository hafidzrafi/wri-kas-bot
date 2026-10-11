"""Shared fixtures and mock infrastructure for E2E test suite."""

import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest
import respx

# Synthetic Test Personas (Zero-PII compliant)
ALICE_PHONE = "6281100000001"
ALICE_NAME = "Alice"
ALICE_JID = f"{ALICE_PHONE}@s.whatsapp.net"

BOB_PHONE = "6281100000002"
BOB_NAME = "Bob"
BOB_JID = f"{BOB_PHONE}@s.whatsapp.net"

CHARLIE_PHONE = "6281100000003"
CHARLIE_NAME = "Charlie"
CHARLIE_JID = f"{CHARLIE_PHONE}@s.whatsapp.net"

TREASURER_PHONE = "6281100000099"
TREASURER_NAME = "Bendahara WRI"
TREASURER_JID = f"{TREASURER_PHONE}@s.whatsapp.net"


@pytest.fixture
def test_personas() -> dict[str, dict[str, str]]:
    """Standard synthetic test personas."""
    return {
        "alice": {"phone": ALICE_PHONE, "name": ALICE_NAME, "jid": ALICE_JID},
        "bob": {"phone": BOB_PHONE, "name": BOB_NAME, "jid": BOB_JID},
        "charlie": {"phone": CHARLIE_PHONE, "name": CHARLIE_NAME, "jid": CHARLIE_JID},
        "treasurer": {"phone": TREASURER_PHONE, "name": TREASURER_NAME, "jid": TREASURER_JID},
    }


@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    """Provide a path to a temporary SQLite database."""
    db_file = tmp_path / "test_kas_wri.db"
    return str(db_file)


@pytest.fixture
def sqlite_schema_sql() -> str:
    """Standard DDL script matching PROJECT.md and specs for SQLite WAL schema."""
    return """
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone_number TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'Aktif',
        balance INTEGER NOT NULL DEFAULT 0,
        last_billed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS monthly_dues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
        year INTEGER NOT NULL DEFAULT 2026,
        month TEXT NOT NULL,
        month_index INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'UNPAID',
        payment_method TEXT,
        proof_url TEXT,
        transaction_id INTEGER REFERENCES transactions(id),
        paid_at TIMESTAMP,
        UNIQUE(member_id, year, month)
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL REFERENCES members(id),
        amount_paid INTEGER NOT NULL,
        payment_method TEXT NOT NULL,
        balance_before INTEGER NOT NULL DEFAULT 0,
        balance_after INTEGER NOT NULL DEFAULT 0,
        months_filled TEXT,
        status TEXT NOT NULL DEFAULT 'PENDING',
        proof_drive_url TEXT,
        local_image_path TEXT,
        ocr_amount INTEGER,
        ocr_bank TEXT,
        rejection_reason TEXT,
        approved_by TEXT,
        wa_message_id TEXT,
        notification_msg_id TEXT,
        sheets_cells_filled TEXT,
        sync_status TEXT NOT NULL DEFAULT 'PENDING',
        sync_error TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS monthly_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period_month TEXT NOT NULL,
        non_cash_amount INTEGER NOT NULL DEFAULT 0,
        cash_amount INTEGER NOT NULL DEFAULT 0,
        prev_balance INTEGER,
        new_balance INTEGER,
        target_row INTEGER,
        sync_status TEXT NOT NULL DEFAULT 'PENDING',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_members_phone ON members(phone_number);
    CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
    CREATE INDEX IF NOT EXISTS idx_transactions_sync ON transactions(sync_status);
    CREATE INDEX IF NOT EXISTS idx_monthly_dues_member
        ON monthly_dues(member_id, year, month_index);
    """


@pytest.fixture
def initialized_db(tmp_db_path: str, sqlite_schema_sql: str) -> str:
    """Create a temporary initialized SQLite database with standard members."""
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(sqlite_schema_sql)

    # Insert synthetic test members (Feb-Dec 2026 dues)
    cursor = conn.cursor()
    members = [
        (ALICE_NAME, ALICE_PHONE, "Aktif", 0),
        (BOB_NAME, BOB_PHONE, "Aktif", 0),
        (CHARLIE_NAME, CHARLIE_PHONE, "Aktif", 0),
    ]
    month_names = [
        (2, "Februari"),
        (3, "Maret"),
        (4, "April"),
        (5, "Mei"),
        (6, "Juni"),
        (7, "Juli"),
        (8, "Agustus"),
        (9, "September"),
        (10, "Oktober"),
        (11, "November"),
        (12, "Desember"),
    ]

    for name, phone, status, balance in members:
        cursor.execute(
            "INSERT INTO members (name, phone_number, status, balance) VALUES (?, ?, ?, ?)",
            (name, phone, status, balance),
        )
        member_id = cursor.lastrowid
        for m_idx, m_name in month_names:
            cursor.execute(
                "INSERT INTO monthly_dues "
                "(member_id, year, month, month_index, status) VALUES (?, 2026, ?, ?, 'UNPAID')",
                (member_id, m_name, m_idx),
            )

    conn.commit()
    conn.close()
    return tmp_db_path


@pytest.fixture
def mock_evolution_api():
    """respx router mocking Evolution API endpoints."""
    with respx.mock(base_url="http://localhost:8080", assert_all_called=False) as respx_mock:
        respx_mock.post(url__regex=r"/message/sendText/.*").respond(
            status_code=201,
            json={"key": {"id": "TEST_WA_MSG_ID", "fromMe": True}, "status": "PENDING"},
        )
        respx_mock.post(url__regex=r"/message/sendMedia/.*").respond(
            status_code=201,
            json={"key": {"id": "TEST_MEDIA_MSG_ID", "fromMe": True}, "status": "PENDING"},
        )
        yield respx_mock


@pytest.fixture
def mock_sheets_client():
    """Mock Google Sheets API client with recorded actions."""
    mock = MagicMock()
    mock.updated_cells = []
    mock.cleared_ranges = []
    mock.appended_rows = []

    def fake_update(name: str, month: str, proof_url: str) -> str:
        coord = "PENGURUS-2025/2026!D5"
        mock.updated_cells.append(
            {"name": name, "month": month, "proof_url": proof_url, "coord": coord}
        )
        return coord

    def fake_batch_clear(cell_ranges: list[str]) -> None:
        mock.cleared_ranges.extend(cell_ranges)

    def fake_append_log(row_data: dict[str, Any]) -> None:
        mock.appended_rows.append(row_data)

    def fake_scan() -> int:
        return 54

    mock.update_payment_month.side_effect = fake_update
    mock.batch_clear_cells.side_effect = fake_batch_clear
    mock.append_log_transaksi.side_effect = fake_append_log
    mock.scan_debit_kredit_bottom_up.side_effect = fake_scan
    return mock


@pytest.fixture
def mock_drive_client():
    """Mock Google Drive client returning deterministic web viewer link."""
    mock = MagicMock()
    mock.uploaded_files = []

    def fake_upload(image_bytes: bytes, filename: str) -> str:
        mock.uploaded_files.append({"filename": filename, "size": len(image_bytes)})
        return f"https://drive.google.com/file/d/mock-id-{filename}/view"

    mock.upload_file.side_effect = fake_upload
    return mock


@pytest.fixture
def make_webhook_payload():
    """Factory helper creating Evolution API v2 JSON webhook payloads."""

    def _create(
        sender_phone: str = ALICE_PHONE,
        message_text: str | None = None,
        image_base64: str | None = None,
        quoted_id: str | None = None,
        quoted_text: str | None = None,
        is_from_me: bool = False,
        remote_jid: str | None = None,
    ) -> dict[str, Any]:
        jid = remote_jid or f"{sender_phone}@s.whatsapp.net"
        payload: dict[str, Any] = {
            "event": "messages.upsert",
            "instance": "kas-wri-test",
            "data": {
                "key": {
                    "remoteJid": jid,
                    "fromMe": is_from_me,
                    "id": "MSG_ID_12345678",
                },
                "pushName": "Test Sender",
                "message": {},
            },
        }

        if quoted_id and quoted_text:
            payload["data"]["message"]["extendedTextMessage"] = {
                "text": message_text or "",
                "contextInfo": {
                    "stanzaId": quoted_id,
                    "participant": TREASURER_JID,
                    "quotedMessage": {
                        "conversation": quoted_text,
                    },
                },
            }
        elif image_base64:
            payload["data"]["message"]["imageMessage"] = {
                "caption": message_text or "",
                "mimetype": "image/jpeg",
                "base64": image_base64,
            }
        else:
            payload["data"]["message"]["conversation"] = message_text or ""

        return payload

    return _create
