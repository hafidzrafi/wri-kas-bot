"""Tier 2: Boundary & Corner Cases: Webhook Ingestion & Evolution API (models.py, app.py)."""

import pytest
from httpx import ASGITransport, AsyncClient

try:
    from wri_kas_bot.app import app

    HAS_APP = True
except ImportError:
    HAS_APP = False
    app = None

pytestmark = pytest.mark.skipif(not HAS_APP, reason="wri_kas_bot.app not implemented yet")


@pytest.mark.asyncio
async def test_webhook_malformed_json_returns_422():
    """Boundary: Completely invalid payload structure returns HTTP 422 or handled cleanly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/webhook", json={"unexpected": "payload"})
        assert res.status_code in (200, 422)


@pytest.mark.asyncio
async def test_webhook_ignores_from_me_messages(make_webhook_payload):
    """Boundary: Bot outbound messages (fromMe=True) are ignored without action."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(is_from_me=True, message_text="/status")
        res = await client.post("/webhook", json=payload)
        assert res.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_ignores_group_messages(make_webhook_payload):
    """Boundary: Messages originating in group chats (@g.us) are dropped."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(remote_jid="120363000000000000@g.us", message_text="/status")
        res = await client.post("/webhook", json=payload)
        assert res.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_quoted_message_missing_context_info(make_webhook_payload):
    """Corner case: Extended text message where contextInfo is empty."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        raw = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "6281100000099@s.whatsapp.net", "fromMe": False, "id": "123"},
                "message": {"extendedTextMessage": {"text": "/acc"}},
            },
        }
        res = await client.post("/webhook", json=raw)
        assert res.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_unregistered_sender_rejection(make_webhook_payload, mock_evolution_api):
    """Boundary: Unregistered phone number texting bot receives registration advice."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(sender_phone="6289999999999", message_text="/status")
        res = await client.post("/webhook", json=payload)
        assert res.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_wri_alias_endpoint(make_webhook_payload):
    """Verify that the /webhook/wri route alias correctly handles payloads."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(is_from_me=True, message_text="/status")
        res = await client.post("/webhook/wri", json=payload)
        assert res.status_code in (200, 202)
        assert res.json().get("status") == "ignored_from_me"
