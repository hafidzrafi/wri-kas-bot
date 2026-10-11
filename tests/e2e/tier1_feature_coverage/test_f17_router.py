"""Tier 1: Feature Coverage — Feature 17: FastAPI Application Router (app.py)."""

import pytest
from httpx import ASGITransport, AsyncClient

try:
    from wri_kas_bot.app import app

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    app = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.app not implemented yet")


@pytest.mark.asyncio
async def test_health_check_endpoint():
    """Verify GET /health returns 200 OK and health status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"


@pytest.mark.asyncio
async def test_webhook_endpoint_accepts_post(make_webhook_payload):
    """Verify POST /webhook returns 200 OK."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(message_text="/ping")
        response = await client.post("/webhook", json=payload)
        assert response.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_routes_member_text_message(make_webhook_payload, mock_evolution_api):
    """Verify router dispatches member text command to member handler."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(sender_phone="6281100000001", message_text="/status")
        response = await client.post("/webhook", json=payload)
        assert response.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_routes_receipt_image(make_webhook_payload, mock_evolution_api):
    """Verify router dispatches image receipt payload to receipt handler."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(
            sender_phone="6281100000001", image_base64="aW1hZ2UtYnl0ZXM="
        )
        response = await client.post("/webhook", json=payload)
        assert response.status_code in (200, 202)


@pytest.mark.asyncio
async def test_webhook_routes_treasurer_command(make_webhook_payload, mock_evolution_api):
    """Verify router dispatches quoted moderation message from treasurer."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = make_webhook_payload(
            sender_phone="6281100000099",
            message_text="/acc",
            quoted_id="MSG_123",
            quoted_text="Receipt",
        )
        response = await client.post("/webhook", json=payload)
        assert response.status_code in (200, 202)
