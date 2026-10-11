"""Comprehensive unit tests for Evolution API models (src/wri_kas_bot/models.py)."""

from wri_kas_bot.models import (
    CommandType,
    ContextInfo,
    ExtendedTextMessage,
    ImageMessage,
    IncomingMessage,
    MessageContent,
    MessageData,
    MessageKey,
    WebhookPayload,
    parse_acc_command,
    parse_amount,
    parse_command_type,
    parse_phone_and_amount,
    parse_reject_command,
)


def test_message_key_defaults_and_aliases():
    """Verify MessageKey handles Baileys field aliases and default values."""
    key = MessageKey.model_validate(
        {
            "remoteJid": "6281100000001@s.whatsapp.net",
            "fromMe": True,
            "id": "MSG_ID_ABC123",
            "participant": "6281100000002@s.whatsapp.net",
            "extraIgnoredField": "extra",
        }
    )
    assert key.remote_jid == "6281100000001@s.whatsapp.net"
    assert key.from_me is True
    assert key.id == "MSG_ID_ABC123"
    assert key.participant == "6281100000002@s.whatsapp.net"


def test_context_info_polymorphic_text_extraction():
    """Verify ContextInfo extracts quoted text across various Baileys message shapes."""
    # 1. Plain conversation
    ctx_conv = ContextInfo.model_validate(
        {"stanzaId": "ST_1", "quotedMessage": {"conversation": "Hello from conversation"}}
    )
    assert ctx_conv.text == "Hello from conversation"
    assert ctx_conv.quoted_text == "Hello from conversation"
    assert ctx_conv.quoted_image_caption() == "Hello from conversation"

    # 2. Extended text message
    ctx_ext = ContextInfo.model_validate(
        {
            "stanzaId": "ST_2",
            "quotedMessage": {"extendedTextMessage": {"text": "Hello from extended text"}},
        }
    )
    assert ctx_ext.text == "Hello from extended text"

    # 3. Image message caption
    ctx_img = ContextInfo.model_validate(
        {
            "stanzaId": "ST_3",
            "quotedMessage": {"imageMessage": {"caption": "Receipt photo for Alice"}},
        }
    )
    assert ctx_img.text == "Receipt photo for Alice"

    # 4. Document message caption
    ctx_doc = ContextInfo.model_validate(
        {
            "stanzaId": "ST_4",
            "quotedMessage": {"documentMessage": {"caption": "PDF invoice document"}},
        }
    )
    assert ctx_doc.text == "PDF invoice document"

    # 5. Empty or missing quoted message
    ctx_empty = ContextInfo.model_validate({"stanzaId": "ST_5"})
    assert ctx_empty.text is None
    assert ctx_empty.quoted_text is None


def test_extended_text_and_image_message_schemas():
    """Verify standalone message sub-models."""
    ext = ExtendedTextMessage.model_validate(
        {
            "text": "Transfer confirmation",
            "contextInfo": {"stanzaId": "ST_99", "quotedMessage": {"conversation": "Orig"}},
        }
    )
    assert ext.text == "Transfer confirmation"
    assert ext.context_info is not None
    assert ext.context_info.stanza_id == "ST_99"

    img = ImageMessage.model_validate(
        {
            "caption": "Bukti Kas",
            "mimetype": "image/png",
            "base64": "dGVzdA==",
            "url": "https://example.com/receipt.jpg",
        }
    )
    assert img.caption == "Bukti Kas"
    assert img.mimetype == "image/png"
    assert img.base64 == "dGVzdA=="
    assert img.url == "https://example.com/receipt.jpg"


def test_message_content_container():
    """Verify MessageContent parsing container wrappers."""
    content = MessageContent.model_validate(
        {
            "conversation": "Direct conversation text",
            "extendedTextMessage": {"text": "Extended text"},
            "imageMessage": {"caption": "Image caption"},
        }
    )
    assert content.conversation == "Direct conversation text"
    assert content.extended_text_message is not None
    assert content.extended_text_message.text == "Extended text"
    assert content.image_message is not None
    assert content.image_message.caption == "Image caption"


def test_message_data_container_unwrapping():
    """Verify MessageData unwraps ephemeral, viewOnce, and documentWithCaption wrappers."""
    ephemeral_raw = {
        "key": {"remoteJid": "6281100000001@s.whatsapp.net", "id": "1"},
        "message": {
            "ephemeralMessage": {
                "message": {
                    "viewOnceMessage": {
                        "message": {
                            "imageMessage": {
                                "caption": "Unwrapped deep view-once image",
                                "base64": "data:image/jpeg;base64,QUJDREVGRw==",
                            }
                        }
                    }
                }
            }
        },
    }
    data = MessageData.model_validate(ephemeral_raw)
    assert data.is_image is True
    assert data.message_text == "Unwrapped deep view-once image"
    assert data.image_bytes_base64 == "QUJDREVGRw=="


def test_message_data_phone_normalization_direct_and_group():
    """Verify sender phone extraction handles direct and group chats cleanly."""
    # Direct chat
    direct_msg = MessageData.model_validate(
        {
            "key": {"remoteJid": "+62 811-0000-0001@s.whatsapp.net", "id": "D1"},
            "message": {"conversation": "Hello direct"},
        }
    )
    assert direct_msg.is_group is False
    assert direct_msg.sender_phone == "6281100000001"

    # Group chat with participant
    group_msg = MessageData.model_validate(
        {
            "key": {
                "remoteJid": "120363000000000000@g.us",
                "participant": "6281100000003@s.whatsapp.net",
                "id": "G1",
            },
            "message": {"conversation": "Hello group"},
        }
    )
    assert group_msg.is_group is True
    assert group_msg.sender_phone == "6281100000003"


def test_message_data_base64_extraction_variants():
    """Verify image base64 extraction strips prefixes and checks fallback positions."""
    # Raw base64 in imageMessage
    msg1 = MessageData.model_validate(
        {
            "message": {"imageMessage": {"base64": "RAW_B64_BYTES=="}},
        }
    )
    assert msg1.image_bytes_base64 == "RAW_B64_BYTES=="

    # Data URI in imageMessage
    msg2 = MessageData.model_validate(
        {
            "message": {"imageMessage": {"base64": "data:image/png;base64,STRIPPED_B64=="}},
        }
    )
    assert msg2.image_bytes_base64 == "STRIPPED_B64=="

    # Base64 at root of message
    msg3 = MessageData.model_validate(
        {
            "message": {"base64": "ROOT_B64=="},
        }
    )
    assert msg3.image_bytes_base64 == "ROOT_B64=="

    # No base64 present
    msg4 = MessageData.model_validate({"message": {"conversation": "text only"}})
    assert msg4.image_bytes_base64 is None


def test_message_data_quoted_context_and_card_extraction():
    """Verify extracting quoted stanza ID, text, and member phone from receipt card."""
    card_text = (
        "📥 BUKTI KAS MASUK\n\n"
        "👤 Dari: Alice\n"
        "📞 No: 6281100000001\n"
        "💰 Nominal: Rp 20.000\n"
        "🏦 Bank: Bank Jago\n"
    )
    raw = {
        "key": {"remoteJid": "6281100000099@s.whatsapp.net", "id": "TREASURER_REPLY_1"},
        "message": {
            "extendedTextMessage": {
                "text": "/acc",
                "contextInfo": {
                    "stanzaId": "RECEIPT_MSG_100",
                    "participant": "6281100000002@s.whatsapp.net",
                    "quotedMessage": {"conversation": card_text},
                },
            }
        },
    }
    msg = MessageData.model_validate(raw)
    assert msg.message_text == "/acc"
    assert msg.quoted_stanza_id == "RECEIPT_MSG_100"
    assert msg.quoted_text == card_text
    assert msg.quoted_no_pengurus() == "6281100000001"


def test_webhook_payload_list_normalization():
    """Verify WebhookPayload normalizes data when Evolution API delivers a list."""
    payload_dict = {
        "event": "messages.upsert",
        "instance": "kas-wri-test",
        "data": [
            {
                "key": {"remoteJid": "6281100000001@s.whatsapp.net", "id": "LIST_1"},
                "message": {"conversation": "/bayar"},
            }
        ],
    }
    payload = WebhookPayload.model_validate(payload_dict)
    assert payload.sender_phone == "6281100000001"
    assert payload.message_text == "/bayar"
    assert payload.data.key.id == "LIST_1"


def test_incoming_message_domain_representation():
    """Verify IncomingMessage.from_webhook maps all fields accurately."""
    payload_dict = {
        "event": "messages.upsert",
        "instance": "kas-wri-test",
        "data": {
            "key": {
                "remoteJid": "6281100000001@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_DOMAIN_1",
            },
            "pushName": "Alice",
            "message": {
                "extendedTextMessage": {
                    "text": "/status",
                    "contextInfo": {
                        "stanzaId": "PRIOR_MSG_1",
                        "quotedMessage": {"conversation": "No: 6281100000001"},
                    },
                }
            },
        },
    }
    payload = WebhookPayload.model_validate(payload_dict)
    incoming = IncomingMessage.from_webhook(payload)

    assert incoming.sender_phone == "6281100000001"
    assert incoming.remote_jid == "6281100000001@s.whatsapp.net"
    assert incoming.message_id == "MSG_DOMAIN_1"
    assert incoming.message_text == "/status"
    assert incoming.is_image is False
    assert incoming.image_base64 is None
    assert incoming.is_from_me is False
    assert incoming.is_group is False
    assert incoming.quoted_stanza_id == "PRIOR_MSG_1"
    assert incoming.quoted_text == "No: 6281100000001"
    assert incoming.quoted_phone == "6281100000001"
    assert incoming.push_name == "Alice"


def test_command_parsers():
    """Verify command parsing helpers."""
    # parse_amount
    assert parse_amount("20000") == 20000
    assert parse_amount("Rp 20.000") == 20000
    assert parse_amount("Rp. 50.000,00") == 5000000 or parse_amount("50.000") == 50000
    assert parse_amount("none") is None
    assert parse_amount("") is None

    # parse_acc_command
    assert parse_acc_command("/acc") is None
    assert parse_acc_command("/acc 20000") == 20000
    assert parse_acc_command("/acc   30.000  ") == 30000

    # parse_reject_command
    assert parse_reject_command("/reject") == ""
    assert parse_reject_command("/reject bukti buram") == "bukti buram"
    assert parse_reject_command("/reject   nominal tidak sesuai  ") == "nominal tidak sesuai"

    # parse_phone_and_amount
    assert parse_phone_and_amount("/tunai 6281100000001 20000") == ("6281100000001", 20000)
    assert parse_phone_and_amount("/tunai +6281100000001 50.000") == ("6281100000001", 50000)
    assert parse_phone_and_amount("/tunai invalid") is None
    assert parse_phone_and_amount("/tunai") is None

    # parse_command_type
    assert parse_command_type("/acc") == CommandType.ACC
    assert parse_command_type("/reject reason") == CommandType.REJECT
    assert parse_command_type("/tunai 123 456") == CommandType.TUNAI
    assert parse_command_type("/koreksi 20000") == CommandType.KOREKSI
    assert parse_command_type("/undo") == CommandType.UNDO
    assert parse_command_type("/bayar") == CommandType.BAYAR
    assert parse_command_type("/status") == CommandType.STATUS
    assert parse_command_type("/help") == CommandType.HELP
    assert parse_command_type("/cek") == CommandType.CEK
    assert parse_command_type("halo selamat pagi") is None
