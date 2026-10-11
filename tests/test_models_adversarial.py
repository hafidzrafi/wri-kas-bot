"""Empirical Adversarial & Stress Test Suite for Evolution API Models and Parsers.

Tests deeply nested Baileys message structures, container unwrapping, quoted context,
missing/null contextInfo resilience, group/direct JID resolution, and command parsing fuzzing.
"""

from wri_kas_bot.evolution import normalize_jid
from wri_kas_bot.models import (
    CommandType,
    ContextInfo,
    IncomingMessage,
    MessageData,
    WebhookPayload,
    parse_acc_command,
    parse_amount,
    parse_command_type,
    parse_phone_and_amount,
    parse_reject_command,
)
from wri_kas_bot.ocr import _parse_amount

# ==============================================================================
# 1. DEEPLY NESTED & CONTAINER UNWRAPPING STRESS TESTS
# ==============================================================================


def test_deeply_nested_multi_layer_wrappers():
    """Stress-test multi-layered nesting: ephemeral -> viewOnce -> documentWithCaption -> image."""
    raw = {
        "key": {"remoteJid": "6281100000001@s.whatsapp.net", "id": "DEEP_1"},
        "message": {
            "ephemeralMessage": {
                "message": {
                    "viewOnceMessage": {
                        "message": {
                            "documentWithCaptionMessage": {
                                "message": {
                                    "imageMessage": {
                                        "caption": "Deeply nested payment proof",
                                        "base64": "data:image/png;base64,QUJDREVGRw==",
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    data = MessageData.model_validate(raw)
    assert data.is_image is True
    assert data.message_text == "Deeply nested payment proof"
    assert data.image_bytes_base64 == "QUJDREVGRw=="


def test_ephemeral_wrapped_extended_text_quoted_context():
    """Verify ephemeralMessage wrapping extendedTextMessage with contextInfo."""
    card = "📥 BUKTI KAS\n📞 No: 6281100000001\n💰 Nominal: Rp 20.000"
    raw = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "6281100000099@s.whatsapp.net", "id": "EPHEMERAL_REPLY_1"},
            "message": {
                "ephemeralMessage": {
                    "message": {
                        "extendedTextMessage": {
                            "text": "/acc 20000",
                            "contextInfo": {
                                "stanzaId": "ORIGINAL_RECEIPT_ST_1",
                                "participant": "6281100000001@s.whatsapp.net",
                                "quotedMessage": {"conversation": card},
                            },
                        }
                    }
                }
            },
        },
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.message_text == "/acc 20000"
    assert payload.quoted_stanza_id == "ORIGINAL_RECEIPT_ST_1"
    assert payload.quoted_text == card
    assert payload.quoted_no_pengurus() == "6281100000001"


def test_quoted_message_ephemeral_wrapper_extraction():
    """Adversarial: Quoted message itself is wrapped in an ephemeralMessage container."""
    card = "📥 BUKTI KAS\n📞 No: 6281100000001\n💰 Nominal: Rp 20.000"
    ctx = ContextInfo.model_validate(
        {
            "stanzaId": "ST_WRAP_1",
            "quotedMessage": {
                "ephemeralMessage": {
                    "message": {
                        "conversation": card,
                    }
                }
            },
        }
    )
    # The current implementation fails to unwrap containers inside quotedMessage
    assert ctx.text == card, (
        f"ContextInfo.text failed to unwrap ephemeralMessage inside quotedMessage: got {ctx.text!r}"
    )


def test_quoted_message_view_once_wrapper_extraction():
    """Adversarial: Quoted message itself is wrapped in viewOnceMessageV2 with image caption."""
    card = "📥 BUKTI KAS\n📞 No: 6281100000002\n💰 Nominal: Rp 10.000"
    ctx = ContextInfo.model_validate(
        {
            "stanzaId": "ST_VO_1",
            "quotedMessage": {
                "viewOnceMessageV2": {
                    "message": {
                        "imageMessage": {
                            "caption": card,
                        }
                    }
                }
            },
        }
    )
    # The current implementation fails to unwrap containers inside quotedMessage
    assert ctx.text == card, (
        f"ContextInfo.text failed to unwrap viewOnceMessage inside quotedMessage: got {ctx.text!r}"
    )


# ==============================================================================
# 2. MALFORMED, NULL, AND MISSING CONTEXT_INFO / KEY BOUNDARIES
# ==============================================================================


def test_null_context_info_in_extended_text_does_not_crash():
    """Adversarial: extendedTextMessage has 'contextInfo': None (null in JSON).

    When Baileys emits extendedTextMessage without quote, 'contextInfo' is often None/null.
    The model must NOT raise a ValidationError when accessing quoted properties or mapping.
    """
    raw = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "6281100000001@s.whatsapp.net", "id": "MSG_NO_QUOTE"},
            "message": {
                "extendedTextMessage": {
                    "text": "/status",
                    "contextInfo": None,
                }
            },
        },
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.message_text == "/status"
    # Accessing these properties must not raise ValidationError
    assert payload.quoted_stanza_id is None
    assert payload.quoted_text is None
    assert payload.quoted_no_pengurus() is None

    # Mapping to IncomingMessage must not raise ValidationError
    incoming = IncomingMessage.from_webhook(payload)
    assert incoming.message_text == "/status"
    assert incoming.quoted_stanza_id is None


def test_null_context_info_in_image_message_does_not_crash():
    """Adversarial: imageMessage has 'contextInfo': None (null in JSON)."""
    raw = {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": "6281100000001@s.whatsapp.net", "id": "IMG_NO_QUOTE"},
            "message": {
                "imageMessage": {
                    "caption": "Bukti kas tanpa reply",
                    "contextInfo": None,
                }
            },
        },
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.quoted_stanza_id is None
    assert payload.quoted_text is None


def test_malformed_context_info_non_dict_types_do_not_crash():
    """Adversarial: contextInfo is an invalid type (string, list, int)."""
    for bad_ctx in ["not-a-dict", [1, 2, 3], 12345]:
        raw = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "6281100000001@s.whatsapp.net", "id": "MALFORMED_CTX"},
                "message": {
                    "extendedTextMessage": {
                        "text": "/help",
                        "contextInfo": bad_ctx,
                    }
                },
            },
        }
        payload = WebhookPayload.model_validate(raw)
        assert payload.quoted_stanza_id is None
        assert payload.quoted_text is None


def test_empty_list_data_payload_normalization():
    """Adversarial: Evolution API emits empty list for data: 'data': []."""
    raw = {
        "event": "messages.upsert",
        "data": [],
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.sender_phone == ""
    assert payload.message_text == ""


def test_missing_or_empty_key_payload():
    """Boundary: Webhook payload completely lacks 'key'."""
    raw = {
        "event": "messages.upsert",
        "data": {
            "message": {"conversation": "hello"},
        },
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.sender_phone == ""
    assert payload.remote_jid == ""
    assert payload.from_me is False
    assert payload.message_text == "hello"


# ==============================================================================
# 3. GROUP VS DIRECT SENDER PHONE & PARTICIPANT EXTRACTION
# ==============================================================================


def test_group_message_sender_phone_uses_participant():
    """Verify group message with remoteJid @g.us extracts phone from participant."""
    raw = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "120363000000000000@g.us",
                "participant": "+62 811-0000-0003@s.whatsapp.net",
                "id": "GRP_MSG_1",
            },
            "message": {"conversation": "/status"},
        },
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.is_group is True
    assert payload.sender_phone == "6281100000003"


def test_direct_message_sender_phone_ignores_irrelevant_participant():
    """Verify 1:1 direct chat uses remoteJid even if stray participant exists."""
    raw = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "6281100000001@s.whatsapp.net",
                "participant": "6289999999999@s.whatsapp.net",
                "id": "DIR_MSG_1",
            },
            "message": {"conversation": "/bayar"},
        },
    }
    payload = WebhookPayload.model_validate(raw)
    assert payload.is_group is False
    assert payload.sender_phone == "6281100000001"


# ==============================================================================
# 4. BASE64 MEDIA HANDLING VARIANTS
# ==============================================================================


def test_base64_data_uri_various_mime_prefixes():
    """Verify base64 stripping works across different image MIME prefixes."""
    prefixes = [
        "data:image/jpeg;base64,",
        "data:image/png;base64,",
        "data:image/webp;base64,",
        "data:application/octet-stream;base64,",
    ]
    raw_payload = "SGVsbG8gV29ybGQ="
    for prefix in prefixes:
        data = MessageData.model_validate(
            {"message": {"imageMessage": {"base64": f"{prefix}{raw_payload}"}}}
        )
        assert data.image_bytes_base64 == raw_payload


def test_base64_raw_without_prefix():
    """Verify raw base64 without prefix is preserved."""
    raw_payload = "QUJDREVGR0hJSktMTU5PUA=="
    data = MessageData.model_validate({"message": {"imageMessage": {"base64": raw_payload}}})
    assert data.image_bytes_base64 == raw_payload


# ==============================================================================
# 5. COMMAND PARSING FUZZING & FINANCIAL ACCURACY
# ==============================================================================


def test_command_type_case_insensitivity_and_spacing():
    """Fuzz command type parsing with leading whitespace, mixed case, newlines."""
    assert parse_command_type("   /ACC   ") == CommandType.ACC
    assert parse_command_type("/AcC 20000") == CommandType.ACC
    assert parse_command_type("/REJECT alasan bukti buram") == CommandType.REJECT
    assert parse_command_type("\n\t/TUNAI 6281100000001 20000") == CommandType.TUNAI
    assert parse_command_type("/undo\n") == CommandType.UNDO
    assert parse_command_type("bukan command") is None


def test_parse_amount_strips_indonesian_decimal_cents():
    """CRITICAL: parse_amount must strip Indonesian decimal cents (,00 or .00).

    In Indonesian banking notation, Rp 20.000,00 is Rp 20,000, NOT Rp 2,000,000.
    """
    assert parse_amount("20.000,00") == 20000, (
        f"parse_amount('20.000,00') failed! Produced {parse_amount('20.000,00')}, "
        "causing a 100x financial balance inflation bug."
    )
    assert parse_amount("Rp 50.000,00") == 50000
    assert parse_amount("Rp. 100.000,00") == 100000
    assert parse_amount("20000.00") == 20000


def test_parse_acc_command_with_cents_notation():
    """Verify /acc command parses amounts with cents notation without 100x inflation."""
    assert parse_acc_command("/acc 20.000,00") == 20000
    assert parse_acc_command("/acc Rp 30.000,00") == 30000
    assert parse_acc_command("/acc") is None
    assert parse_acc_command("/acc   ") is None


def test_parse_reject_command_whitespace_fuzzing():
    """Verify /reject command preserves reason across weird spacing."""
    assert parse_reject_command("/reject    nominal kurang   ") == "nominal kurang"
    assert parse_reject_command("/REJECT \n bukti transfer palsu") == "bukti transfer palsu"
    assert parse_reject_command("/reject") == ""
    assert parse_reject_command("/reject   ") == ""


def test_parse_phone_and_amount_with_formatted_phone():
    """Adversarial: /tunai command where phone number contains internal spaces."""
    # When user pastes formatted WhatsApp number '+62 811-0000-0001'
    result = parse_phone_and_amount("/tunai +62 811-0000-0001 20000")
    assert result == ("6281100000001", 20000), (
        f"parse_phone_and_amount failed on spaced phone number! Got {result!r}"
    )


def test_quoted_no_pengurus_with_plus_prefix():
    """Adversarial: forwarded receipt card contains 'No: +6281100000001'."""
    card = "BUKTI KAS\n👤 Nama: Alice\n📞 No: +6281100000001\n💰 Rp 20.000"
    raw = {
        "key": {"remoteJid": "6281100000099@s.whatsapp.net", "id": "REPLY_1"},
        "message": {
            "extendedTextMessage": {
                "text": "/acc",
                "contextInfo": {
                    "stanzaId": "ST_CARD",
                    "quotedMessage": {
                        "conversation": card,
                    },
                },
            }
        },
    }
    data = MessageData.model_validate(raw)
    assert data.quoted_no_pengurus() == "6281100000001", (
        f"quoted_no_pengurus failed on '+62...' phone number: got {data.quoted_no_pengurus()!r}"
    )


# ==============================================================================
# 6. EVOLUTION CLIENT & OCR PARSER ADVERSARIAL CASES
# ==============================================================================


def test_normalize_jid_with_embedded_spaces_in_jid():
    """Verify normalize_jid strips internal spaces even when ending with @s.whatsapp.net."""
    raw_jid = "+62 811-0000-0001@s.whatsapp.net"
    normalized = normalize_jid(raw_jid)
    assert normalized == "6281100000001@s.whatsapp.net", (
        f"normalize_jid returned un-sanitized string: {normalized!r}"
    )


def test_ocr_parse_amount_rejects_boolean_values():
    """Verify OCR amount parser rejects boolean True/False."""
    assert _parse_amount(True) is None, (
        f"_parse_amount(True) evaluated to {_parse_amount(True)!r} instead of None"
    )
    assert _parse_amount(False) is None
