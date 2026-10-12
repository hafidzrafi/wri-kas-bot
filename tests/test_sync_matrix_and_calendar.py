"""Unit tests for sheet payment matrix, calendar dues, and webhook deduplication."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from wri_kas_bot.app import app
from wri_kas_bot.database import Database
from wri_kas_bot.evolution import EvolutionClient
from wri_kas_bot.handlers.member_handler import handle_member
from wri_kas_bot.models import WebhookPayload
from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository
from wri_kas_bot.sync import sync_members_from_sheets


def _make_payload(
    text: str, phone: str = "6281100000001", message_id: str = "MSG_001"
) -> WebhookPayload:
    return WebhookPayload.model_validate(
        {
            "event": "messages.upsert",
            "instance": "Bot WRI",
            "data": {
                "key": {
                    "remoteJid": f"{phone}@s.whatsapp.net",
                    "fromMe": False,
                    "id": message_id,
                },
                "pushName": "Alice",
                "message": {"conversation": text},
                "messageType": "conversation",
                "messageTimestamp": int(time.time()),
            },
        }
    )


@pytest.fixture
def test_db(tmp_path) -> Database:
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    with db.transaction() as conn:
        MemberRepository(conn).create(
            name="Alice",
            phone_number="6281100000001",
            status="Aktif",
            balance=0,
        )
        MonthlyDuesRepository(conn).init_member_dues(member_id=1, year=2026)
    return db


@pytest.mark.asyncio
async def test_sync_members_with_payment_matrix(tmp_path):
    """Verify sync_members_from_sheets applies pre-existing payments from sheet matrix."""
    db = Database(str(tmp_path / "sync_test.db"))
    db.init_schema()

    mock_sheets = MagicMock()
    mock_sheets.fetch_all_members.return_value = [
        {
            "name": "Alice Wonderland",
            "phone_number": "6281100000001",
            "status": "Aktif",
            "deposit_balance": 0,
        }
    ]
    mock_sheets.fetch_payment_matrix.return_value = {
        "alice wonderland": {
            "Februari": "Bank Jago",
            "Maret": "Bank Jago",
            "April": "Bank Jago",
            "Mei": "Bank Jago",
            "Juni": "Bank Jago",
            "Juli": "Bank Jago",
            "Agustus": "Bank Jago",
        }
    }

    synced = sync_members_from_sheets(db, sheets_client=mock_sheets, year=2026)
    assert synced == 1

    with db.connect() as conn:
        dues_repo = MonthlyDuesRepository(conn)
        paid = dues_repo.get_paid(member_id=1, year=2026)
        unpaid = dues_repo.get_unpaid(member_id=1, year=2026)

        assert len(paid) == 7
        assert [d.month for d in paid] == [
            "Februari",
            "Maret",
            "April",
            "Mei",
            "Juni",
            "Juli",
            "Agustus",
        ]
        assert len(unpaid) == 4
        assert [d.month for d in unpaid] == ["September", "Oktober", "November", "Desember"]


@pytest.mark.asyncio
async def test_member_status_option_1_calendar_split(test_db):
    """Verify /status separates overdue dues (up to current month) from future dues."""
    db = test_db
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_001"}})

    # Seed Alice as paid Feb through Aug (7 months)
    with db.connect() as conn:
        dues_repo = MonthlyDuesRepository(conn)
        for m_idx in range(2, 9):
            dues_repo.mark_paid_by_month(
                member_id=1,
                year=2026,
                month_index=m_idx,
                payment_method="Bank Jago",
            )

    payload = _make_payload("/status", phone="6281100000001")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    assert res["overdue_count"] == 2  # September, Oktober in October 2026
    assert res["future_count"] == 2  # November, Desember
    assert res["unpaid_count"] == 4
    assert res["net_bill"] == 20000

    evo.send_text.assert_awaited_once()
    msg = evo.send_text.call_args.kwargs["text"]
    assert "Februari, Maret, April, Mei, Juni, Juli, Agustus (7 bln)" in msg
    assert "September, Oktober (2 bln)" in msg
    assert "*Tagihan:* Rp 20,000" in msg
    assert "November, Desember (2 bln)" in msg


@pytest.mark.asyncio
async def test_member_status_with_deposit_balance(test_db):
    """Verify deposit balance correctly offsets overdue amount in status response."""
    db = test_db
    evo = EvolutionClient()
    evo.send_text = AsyncMock(return_value={"key": {"id": "RESP_002"}})

    # Seed Alice as paid Feb through Aug (7 months) and give 5,000 balance
    with db.connect() as conn:
        dues_repo = MonthlyDuesRepository(conn)
        for m_idx in range(2, 9):
            dues_repo.mark_paid_by_month(
                member_id=1,
                year=2026,
                month_index=m_idx,
                payment_method="Bank Jago",
            )
        MemberRepository(conn).update_balance(member_id=1, new_balance=5000)

    payload = _make_payload("/status", phone="6281100000001")
    res = await handle_member(payload, db=db, evolution=evo)

    assert res["status"] == "ok"
    assert res["overdue_count"] == 2
    assert res["net_bill"] == 15000  # 20,000 overdue - 5,000 balance

    msg = evo.send_text.call_args.kwargs["text"]
    assert "*Tagihan:* Rp 15,000" in msg


@pytest.mark.asyncio
async def test_webhook_deduplication_idempotency(test_db):
    """Verify repeated deliveries with same message_id are ignored."""
    from httpx import ASGITransport, AsyncClient

    db = test_db
    app.state.db = db
    app.state.evolution = EvolutionClient()
    app.state.evolution.send_text = AsyncMock(return_value={"key": {"id": "RESP_003"}})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload_data = {
            "event": "messages.upsert",
            "instance": "Bot WRI",
            "data": {
                "key": {
                    "remoteJid": "6281100000001@s.whatsapp.net",
                    "fromMe": False,
                    "id": "UNIQUE_DEDUP_ID_999",
                },
                "pushName": "Alice",
                "message": {"conversation": "/help"},
                "messageType": "conversation",
                "messageTimestamp": int(time.time()),
            },
        }

        # First delivery: processed
        resp1 = await client.post("/webhook", json=payload_data)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "ok"

        # Second delivery (duplicate message_id): ignored
        resp2 = await client.post("/webhook", json=payload_data)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "ignored_duplicate"


@pytest.mark.asyncio
async def test_member_bayar_dispatches_qris_image(test_db, monkeypatch):
    """Verify /bayar sends QRIS image with caption when image is available."""
    import wri_kas_bot.handlers.member_handler as mh

    monkeypatch.setattr(mh, "_CACHED_QRIS_BASE64", "MOCK_BASE64_QRIS_BYTES")

    evo = EvolutionClient()
    evo.send_image_base64 = AsyncMock(return_value="RESP_IMG_001")
    evo.send_text = AsyncMock(return_value="RESP_TEXT_001")

    payload = _make_payload("/bayar", phone="6281100000001")
    res = await handle_member(payload, db=test_db, evolution=evo)

    assert res["status"] == "ok"
    assert res["media"] == "image"
    evo.send_image_base64.assert_awaited_once()
    caption = evo.send_image_base64.call_args.kwargs["caption"]
    assert "PEMBAYARAN KAS WRI" in caption
    assert "Bank Jago" in caption
    assert "Scan kode QR pada gambar di atas" in caption
    evo.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_bayar_fallbacks_to_text(test_db, monkeypatch):
    """Verify /bayar gracefully fallbacks to send_text if QRIS image is unavailable."""
    import wri_kas_bot.handlers.member_handler as mh

    monkeypatch.setattr(mh, "_CACHED_QRIS_BASE64", None)

    evo = EvolutionClient()
    evo.send_image_base64 = AsyncMock(side_effect=RuntimeError("Evolution media failure"))
    evo.send_text = AsyncMock(return_value="RESP_TEXT_002")

    payload = _make_payload("/bayar", phone="6281100000001")
    res = await handle_member(payload, db=test_db, evolution=evo)

    assert res["status"] == "ok"
    assert res["media"] == "text"
    evo.send_text.assert_awaited_once()
    msg = evo.send_text.call_args.kwargs["text"]
    assert "PEMBAYARAN KAS WRI" in msg
    assert "Bank Jago" in msg
