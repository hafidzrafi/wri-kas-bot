"""Unit tests for Evolution API client."""

import httpx
import pytest
import respx

from wri_kas_bot.config import Settings
from wri_kas_bot.evolution import EvolutionAPIError, EvolutionClient, normalize_jid


def test_normalize_jid_valid_and_invalid():
    """Verify phone and JID normalization across various input formats."""
    # Plain digits
    assert normalize_jid("6281100000001") == "6281100000001@s.whatsapp.net"

    # With formatting
    assert normalize_jid("+62 811-0000-0001 ") == "6281100000001@s.whatsapp.net"

    # Already formatted WhatsApp user JID
    assert normalize_jid("6281100000001@s.whatsapp.net") == "6281100000001@s.whatsapp.net"

    # WhatsApp group JID
    assert normalize_jid("120363000000000000@g.us") == "120363000000000000@g.us"

    # Invalid empty or non-digit input
    with pytest.raises(ValueError, match="Invalid recipient phone number"):
        normalize_jid("")

    with pytest.raises(ValueError, match="Invalid recipient phone number"):
        normalize_jid("   +++ ---   ")


def test_evolution_client_init_and_config():
    """Verify EvolutionClient initialization with custom parameters vs fallback settings."""
    # Explicit parameters
    client = EvolutionClient(
        base_url="http://custom-host:8080/",
        api_key="secret-key-123",
        instance="custom-bot",
        timeout=15.0,
    )
    assert client.base_url == "http://custom-host:8080"
    assert client.api_key == "secret-key-123"
    assert client.instance == "custom-bot"
    assert client.headers == {
        "apikey": "secret-key-123",
        "Content-Type": "application/json",
    }

    # Settings object injection
    custom_settings = Settings(
        evolution_api_url="http://settings-host:9000",
        evolution_api_key="settings-key",
        bot_instance_name="settings-instance",
    )
    client_from_cfg = EvolutionClient(settings=custom_settings)
    assert client_from_cfg.base_url == "http://settings-host:9000"
    assert client_from_cfg.api_key == "settings-key"
    assert client_from_cfg.instance == "settings-instance"


@pytest.mark.asyncio
async def test_evolution_client_context_manager():
    """Verify async context manager lifecycle for httpx connection pool."""
    async with EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock-key",
        instance="test-bot",
    ) as client:
        internal_client = await client._get_client()
        assert internal_client is not None
        assert not internal_client.is_closed

    # Once exited, client should be closed
    assert client._client is None


@pytest.mark.asyncio
async def test_send_text_url_quoting_and_payload(respx_mock: respx.MockRouter):
    """Verify send_text properly quotes instance names with spaces and formats payload."""
    instance_name = "WRI Bot Dues"
    route = respx_mock.post("http://localhost:8080/message/sendText/WRI%20Bot%20Dues").respond(
        status_code=201,
        json={"key": {"id": "MSG_ID_TEST_100"}, "status": "PENDING"},
    )

    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="my-key",
        instance=instance_name,
    )
    msg_id = await client.send_text("6281100000001", "Halo Alice, iuran kas masuk")
    await client.close()

    assert msg_id == "MSG_ID_TEST_100"
    assert route.called
    req = route.calls.last.request
    assert req.headers["apikey"] == "my-key"
    assert req.headers["content-type"] == "application/json"
    body = req.read().decode("utf-8")
    assert '"number":"6281100000001@s.whatsapp.net"' in body.replace(" ", "")
    assert "Halo Alice" in body


@pytest.mark.asyncio
async def test_send_image_base64_data_uri_formatting(respx_mock: respx.MockRouter):
    """Verify send_image_base64 automatically applies Data URI format without double prefixing."""
    route = respx_mock.post("http://localhost:8080/message/sendMedia/kas-wri").respond(
        status_code=201,
        json={"key": {"id": "MSG_MEDIA_200"}},
    )

    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="my-key",
        instance="kas-wri",
    )

    # 1. Raw base64 string
    await client.send_image_base64(
        jid="6281100000099@s.whatsapp.net",
        image_base64="aW1hZ2UtYnl0ZXM=",
        caption="Bukti Pembayaran Alice",
    )
    body1 = route.calls.last.request.read().decode("utf-8")
    assert '"media":"aW1hZ2UtYnl0ZXM="' in body1.replace(" ", "")
    assert '"mediatype":"image"' in body1.replace(" ", "")
    assert '"mimetype":"image/jpeg"' in body1.replace(" ", "")
    assert '"fileName":"receipt.jpg"' in body1.replace(" ", "")

    # 2. Pre-formatted data URI string (prefix stripped to raw base64 for Evolution API v2)
    await client.send_image_base64(
        jid="6281100000099@s.whatsapp.net",
        image_base64="data:image/png;base64,YWJjZGVm",
        caption="Pre-formatted",
    )
    body2 = route.calls.last.request.read().decode("utf-8")
    assert '"media":"YWJjZGVm"' in body2.replace(" ", "")
    assert "data:image" not in body2

    await client.close()


@pytest.mark.asyncio
async def test_message_id_fallback_extraction():
    """Verify message ID extraction handles top-level id or list-wrapped payloads."""
    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock",
        instance="kas-wri",
    )

    # Top-level id
    assert client._extract_message_id({"id": "TOP_ID_1"}) == "TOP_ID_1"

    # messageId
    assert client._extract_message_id({"messageId": "MSG_ID_2"}) == "MSG_ID_2"

    # List of items
    assert client._extract_message_id([{"key": {"id": "LIST_KEY_ID"}}]) == "LIST_KEY_ID"

    # Unknown fallback
    assert client._extract_message_id({}) == "UNKNOWN_MSG_ID"


@pytest.mark.asyncio
async def test_evolution_client_http_error_handling(respx_mock: respx.MockRouter):
    """Verify HTTP errors raise EvolutionAPIError with accurate metadata."""
    respx_mock.post("http://localhost:8080/message/sendText/kas-wri").respond(
        status_code=400,
        text="Recipient phone number not registered on WhatsApp",
    )

    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock",
        instance="kas-wri",
    )

    with pytest.raises(EvolutionAPIError) as exc_info:
        await client.send_text("6281100000001", "test")

    err = exc_info.value
    assert err.status_code == 400
    assert "Recipient phone number not registered" in err.response_body
    assert "sendText" in str(err)

    await client.close()


@pytest.mark.asyncio
async def test_evolution_client_network_transport_failure(respx_mock: respx.MockRouter):
    """Verify network connection errors or timeouts raise EvolutionAPIError."""
    respx_mock.post("http://localhost:8080/message/sendText/kas-wri").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock",
        instance="kas-wri",
    )

    with pytest.raises(EvolutionAPIError) as exc_info:
        await client.send_text("6281100000001", "test")

    assert "HTTP transport failure" in str(exc_info.value)
    await client.close()


@pytest.mark.asyncio
async def test_get_base64_from_media_message(respx_mock: respx.MockRouter):
    """Verify get_base64_from_media_message requests media data and strips data URI."""
    respx_mock.post("http://localhost:8080/chat/getBase64FromMediaMessage/kas-wri").respond(
        status_code=200,
        json={"base64": "data:image/jpeg;base64,REVXT0FERURfQllURVM="},
    )

    client = EvolutionClient(
        base_url="http://localhost:8080",
        api_key="mock",
        instance="kas-wri",
    )

    key = {"remoteJid": "6281100000001@s.whatsapp.net", "id": "123"}
    message = {"imageMessage": {}}
    b64 = await client.get_base64_from_media_message(key, message)

    assert b64 == "REVXT0FERURfQllURVM="
    await client.close()
