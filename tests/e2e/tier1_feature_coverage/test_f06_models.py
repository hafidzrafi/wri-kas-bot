"""Tier 1: Feature Coverage — Feature 6: Evolution API Models (models.py)."""

import pytest

try:
    from wri_kas_bot.models import WebhookPayload

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    WebhookPayload = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.models not implemented yet")


def test_parse_plain_text_webhook(make_webhook_payload):
    """Verify parsing standard conversation text webhook."""
    raw = make_webhook_payload(sender_phone="6281100000001", message_text="/status")
    payload = (
        WebhookPayload.model_validate(raw)
        if hasattr(WebhookPayload, "model_validate")
        else WebhookPayload(**raw)
    )

    assert payload.sender_phone == "6281100000001"
    assert payload.message_text == "/status"
    assert payload.is_image is False


def test_parse_image_webhook(make_webhook_payload):
    """Verify parsing image receipt webhook with base64 data."""
    raw = make_webhook_payload(
        sender_phone="6281100000001",
        image_base64="dGVzdC1pbWFnZS1ieXRlcw==",
        message_text="Bukti kas Feb",
    )
    payload = (
        WebhookPayload.model_validate(raw)
        if hasattr(WebhookPayload, "model_validate")
        else WebhookPayload(**raw)
    )

    assert payload.is_image is True
    assert payload.sender_phone == "6281100000001"
    assert payload.image_bytes_base64 == "dGVzdC1pbWFnZS1ieXRlcw=="


def test_parse_quoted_text_webhook(make_webhook_payload):
    """Verify extracting extendedTextMessage.contextInfo for quoted moderation."""
    raw = make_webhook_payload(
        sender_phone="6281100000099",
        message_text="/acc",
        quoted_id="ORIGINAL_MSG_ID_999",
        quoted_text="BUKTI KAS MASUK\n👤 Dari: Alice\n📞 No: 6281100000001",
    )
    payload = (
        WebhookPayload.model_validate(raw)
        if hasattr(WebhookPayload, "model_validate")
        else WebhookPayload(**raw)
    )

    assert payload.message_text == "/acc"
    assert payload.quoted_stanza_id == "ORIGINAL_MSG_ID_999"
    assert "Alice" in (payload.quoted_text or "")


def test_parse_detects_from_me(make_webhook_payload):
    """Verify fromMe flag is captured to ignore outbound bot replies."""
    raw = make_webhook_payload(is_from_me=True, message_text="Hello")
    payload = (
        WebhookPayload.model_validate(raw)
        if hasattr(WebhookPayload, "model_validate")
        else WebhookPayload(**raw)
    )

    is_from_me = getattr(payload, "from_me", getattr(payload, "is_from_me", False))
    assert is_from_me is True


def test_parse_detects_group_jid(make_webhook_payload):
    """Verify group message remoteJid (@g.us) is recognized."""
    raw = make_webhook_payload(remote_jid="120363000000000000@g.us", message_text="/status")
    payload = (
        WebhookPayload.model_validate(raw)
        if hasattr(WebhookPayload, "model_validate")
        else WebhookPayload(**raw)
    )

    is_group = getattr(payload, "is_group", ("@g.us" in str(getattr(payload, "remote_jid", ""))))
    assert is_group is True
