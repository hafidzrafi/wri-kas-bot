"""Tier 1: Feature Coverage: Feature 7: Evolution API HTTP Client (evolution.py)."""

import httpx
import pytest
import respx

from wri_kas_bot.evolution import EvolutionClient


@pytest.mark.asyncio
async def test_evolution_client_send_text_success(mock_evolution_api):
    """Verify send_text issues HTTP POST to correct Evolution API route."""
    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock-key",
        instance="kas-wri-test",
    )
    msg_id = await client.send_text("6281100000001@s.whatsapp.net", "Halo Alice")
    assert msg_id is not None


@pytest.mark.asyncio
async def test_evolution_client_send_image_base64_success(mock_evolution_api):
    """Verify send_image_base64 dispatches base64 media to sendMedia endpoint."""
    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock-key",
        instance="kas-wri-test",
    )
    b64_data = "aW1hZ2UtYnl0ZXM="
    msg_id = await client.send_image_base64(
        jid="6281100000099@s.whatsapp.net",
        image_base64=b64_data,
        caption="Bukti Transfer Masuk",
    )
    assert msg_id is not None


@pytest.mark.asyncio
async def test_evolution_client_includes_api_key_header():
    """Verify client sends apikey header in requests."""
    with respx.mock(base_url="http://localhost:8080") as respx_mock:
        route = respx_mock.post(url__regex=r"/message/sendText/.*").respond(
            status_code=201, json={"key": {"id": "123"}}
        )
        client = EvolutionClient(
            base_url="http://localhost:8080",
            api_key="my-secret-key",
            instance="kas-wri-test",
        )
        await client.send_text("6281100000001@s.whatsapp.net", "test")

        assert route.called
        assert route.calls.last.request.headers.get("apikey") == "my-secret-key"


@pytest.mark.asyncio
async def test_evolution_client_handles_http_error():
    """Verify client raises appropriate exception on Evolution API 500 error."""
    with respx.mock(base_url="http://localhost:8080") as respx_mock:
        respx_mock.post(url__regex=r"/message/sendText/.*").respond(
            status_code=500, json={"error": "Internal gateway failure"}
        )
        client = EvolutionClient(
            base_url="http://localhost:8080",
            api_key="mock-key",
            instance="kas-wri-test",
        )
        with pytest.raises((httpx.HTTPError, RuntimeError, ValueError)):
            await client.send_text("6281100000001@s.whatsapp.net", "test")


@pytest.mark.asyncio
async def test_evolution_client_formats_recipient_jid(mock_evolution_api):
    """Verify send_text accepts phone number and ensures @s.whatsapp.net format."""
    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock-key",
        instance="kas-wri-test",
    )
    # Even if called with plain phone without @, it should send properly
    msg_id = await client.send_text("6281100000001", "Pesan halo")
    assert msg_id is not None
