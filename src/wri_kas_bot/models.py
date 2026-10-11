"""Pydantic v2 schemas and models for Evolution API v2 WhatsApp webhooks.

Provides models to parse incoming messages, quoted context (replies),
base64 receipt attachments, and command extraction for wri-kas-bot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageKey(BaseModel):
    """WhatsApp Baileys message key tuple identifying message source and direction."""

    remote_jid: str = Field(default="", alias="remoteJid")
    from_me: bool = Field(default=False, alias="fromMe")
    id: str = Field(default="")
    participant: str | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


def _unwrap_quoted_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Recursively unwrap Baileys container messages inside quotedMessage."""
    for wrapper in (
        "ephemeralMessage",
        "viewOnceMessage",
        "viewOnceMessageV2",
        "documentWithCaptionMessage",
    ):
        if wrapper in msg and isinstance(msg[wrapper], dict):
            inner = msg[wrapper].get("message")
            if isinstance(inner, dict):
                return _unwrap_quoted_message(inner)
    return msg


class ContextInfo(BaseModel):
    """Quoted context information embedded when a message replies to another message."""

    stanza_id: str | None = Field(default=None, alias="stanzaId")
    participant: str | None = Field(default=None)
    quoted_message: dict[str, Any] | None = Field(default=None, alias="quotedMessage")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @property
    def text(self) -> str | None:
        """Extract text content from quotedMessage regardless of Baileys type."""
        if not self.quoted_message or not isinstance(self.quoted_message, dict):
            return None
        unwrapped = _unwrap_quoted_message(self.quoted_message)
        # 1. Plain conversation
        if "conversation" in unwrapped and unwrapped["conversation"]:
            return str(unwrapped["conversation"])
        # 2. Extended text message
        ext = unwrapped.get("extendedTextMessage")
        if isinstance(ext, dict) and ext.get("text"):
            return str(ext["text"])
        # 3. Image message caption
        img = unwrapped.get("imageMessage")
        if isinstance(img, dict) and img.get("caption"):
            return str(img["caption"])
        # 4. Document message caption
        doc = unwrapped.get("documentMessage")
        if isinstance(doc, dict) and doc.get("caption"):
            return str(doc["caption"])
        return None

    @property
    def quoted_text(self) -> str | None:
        """Helper returning quoted message text."""
        return self.text

    def quoted_image_caption(self) -> str | None:
        """Backward-compatible helper returning quoted message text."""
        return self.text


class ExtendedTextMessage(BaseModel):
    """Baileys extendedTextMessage structure holding text and reply contextInfo."""

    text: str = ""
    context_info: ContextInfo | None = Field(default=None, alias="contextInfo")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ImageMessage(BaseModel):
    """Baileys imageMessage structure holding caption, mimetype, and base64/url data."""

    caption: str = ""
    mimetype: str = "image/jpeg"
    base64: str | None = None
    url: str | None = None
    context_info: ContextInfo | None = Field(default=None, alias="contextInfo")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MessageContent(BaseModel):
    """Union-like container for WhatsApp message body types and wrappers."""

    conversation: str | None = None
    extended_text_message: ExtendedTextMessage | None = Field(
        default=None, alias="extendedTextMessage"
    )
    image_message: ImageMessage | None = Field(default=None, alias="imageMessage")
    ephemeral_message: dict[str, Any] | None = Field(default=None, alias="ephemeralMessage")
    view_once_message: dict[str, Any] | None = Field(default=None, alias="viewOnceMessage")
    view_once_message_v2: dict[str, Any] | None = Field(default=None, alias="viewOnceMessageV2")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MessageData(BaseModel):
    """Payload data object emitted by Evolution API v2 for messages.upsert."""

    key: MessageKey = Field(default_factory=MessageKey)
    push_name: str | None = Field(default=None, alias="pushName")
    message: dict[str, Any] | MessageContent = Field(default_factory=dict)
    message_type: str = Field(alias="messageType", default="")
    message_timestamp: int | str = Field(alias="messageTimestamp", default=0)
    context_info_direct: ContextInfo | None = Field(default=None, alias="contextInfo")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def _unwrap_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Recursively unwrap Baileys container messages (ephemeral, viewOnce, etc.)."""
        for wrapper in (
            "ephemeralMessage",
            "viewOnceMessage",
            "viewOnceMessageV2",
            "documentWithCaptionMessage",
        ):
            if wrapper in msg and isinstance(msg[wrapper], dict):
                inner = msg[wrapper].get("message")
                if isinstance(inner, dict):
                    return self._unwrap_message(inner)
        return msg

    @property
    def unwrapped_message(self) -> dict[str, Any]:
        """Return the inner message dictionary after unwrapping containers."""
        raw_msg = (
            self.message.model_dump(by_alias=True)
            if isinstance(self.message, BaseModel)
            else self.message
        )
        if not isinstance(raw_msg, dict):
            return {}
        return self._unwrap_message(raw_msg)

    @property
    def is_image(self) -> bool:
        """Check if incoming message is an image attachment."""
        msg = self.unwrapped_message
        return self.message_type == "imageMessage" or "imageMessage" in msg

    @property
    def is_group(self) -> bool:
        """Check if message originated from a group chat."""
        return "@g.us" in self.key.remote_jid

    @property
    def from_me(self) -> bool:
        """Check if message originated from the bot itself."""
        return self.key.from_me

    @property
    def is_from_me(self) -> bool:
        """Check if message originated from the bot itself."""
        return self.key.from_me

    @property
    def remote_jid(self) -> str:
        """Return remote JID of sender."""
        return self.key.remote_jid

    @property
    def sender_phone(self) -> str:
        """Extract sanitized phone digits from sender JID or participant."""
        target = (
            self.key.participant
            if (self.is_group and self.key.participant)
            else self.key.remote_jid
        )
        clean = target.split("@")[0] if "@" in target else target
        return re.sub(r"\D", "", clean)

    @property
    def message_text(self) -> str:
        """Extract text from extendedTextMessage, imageMessage caption, or conversation."""
        msg = self.unwrapped_message
        ext = msg.get("extendedTextMessage")
        if isinstance(ext, dict) and ext.get("text"):
            return str(ext["text"])
        img = msg.get("imageMessage")
        if isinstance(img, dict) and img.get("caption"):
            return str(img["caption"])
        conv = msg.get("conversation")
        if conv:
            return str(conv)
        return ""

    @property
    def image_bytes_base64(self) -> str | None:
        """Extract clean base64 image data without data URI prefix."""
        msg = self.unwrapped_message
        img = msg.get("imageMessage")
        b64 = None
        if isinstance(img, dict):
            b64 = img.get("base64")
        if not b64:
            b64 = msg.get("base64")
        if not b64 and isinstance(self.message, dict):
            b64 = self.message.get("base64")
        if b64 and isinstance(b64, str):
            if "," in b64:
                return b64.split(",", 1)[1]
            return b64
        return None

    @property
    def context_info(self) -> ContextInfo | None:
        """Extract contextInfo across direct, extendedTextMessage, and imageMessage."""
        if self.context_info_direct:
            return self.context_info_direct
        msg = self.unwrapped_message
        for wrapper in ("extendedTextMessage", "imageMessage"):
            sub = msg.get(wrapper)
            if isinstance(sub, dict):
                ctx_raw = sub.get("contextInfo")
                if isinstance(ctx_raw, dict):
                    return ContextInfo.model_validate(ctx_raw)
        return None

    @property
    def quoted_stanza_id(self) -> str | None:
        """Extract stanza ID of quoted message."""
        ctx = self.context_info
        return ctx.stanza_id if ctx else None

    @property
    def quoted_text(self) -> str | None:
        """Extract text of quoted message."""
        ctx = self.context_info
        return ctx.text if ctx else None

    def quoted_no_pengurus(self) -> str | None:
        """Extract member phone number from quoted receipt card caption."""
        txt = self.quoted_text
        if not txt:
            return None
        match = re.search(r"No:\s*([+\d\s-]+)", txt)
        if match:
            phone = re.sub(r"\D", "", match.group(1))
            if phone.startswith("0"):
                phone = "62" + phone[1:]
            return phone or None
        return None


class WebhookPayload(BaseModel):
    """Top-level webhook model emitted by Evolution API v2."""

    event: str = "messages.upsert"
    instance: str = ""
    data: MessageData = Field(default_factory=MessageData)
    destination: str | None = None
    date_time: str | None = None
    sender: str | None = None
    server_url: str | None = None
    apikey: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def normalize_data_payload(cls, values: Any) -> Any:
        """Handle Evolution API payloads where data is wrapped in a list or empty."""
        if isinstance(values, dict):
            data_val = values.get("data")
            if isinstance(data_val, list):
                if len(data_val) > 0:
                    values["data"] = data_val[0]
                else:
                    values["data"] = {}
        return values

    @property
    def sender_phone(self) -> str:
        return self.data.sender_phone

    @property
    def message_text(self) -> str:
        return self.data.message_text

    @property
    def is_image(self) -> bool:
        return self.data.is_image

    @property
    def image_bytes_base64(self) -> str | None:
        return self.data.image_bytes_base64

    @property
    def quoted_stanza_id(self) -> str | None:
        return self.data.quoted_stanza_id

    @property
    def quoted_text(self) -> str | None:
        return self.data.quoted_text

    @property
    def from_me(self) -> bool:
        return self.data.key.from_me

    @property
    def is_from_me(self) -> bool:
        return self.data.key.from_me

    @property
    def is_group(self) -> bool:
        return self.data.is_group

    @property
    def remote_jid(self) -> str:
        return self.data.key.remote_jid

    def quoted_no_pengurus(self) -> str | None:
        return self.data.quoted_no_pengurus()


@dataclass
class IncomingMessage:
    """Normalized domain representation of an incoming WhatsApp message."""

    sender_phone: str
    remote_jid: str
    message_id: str
    message_text: str
    is_image: bool
    image_base64: str | None
    is_from_me: bool
    is_group: bool
    quoted_stanza_id: str | None
    quoted_text: str | None
    quoted_phone: str | None
    push_name: str | None = None

    @classmethod
    def from_webhook(cls, payload: WebhookPayload) -> IncomingMessage:
        return cls(
            sender_phone=payload.sender_phone,
            remote_jid=payload.remote_jid,
            message_id=payload.data.key.id,
            message_text=payload.message_text,
            is_image=payload.is_image,
            image_base64=payload.image_bytes_base64,
            is_from_me=payload.from_me,
            is_group=payload.is_group,
            quoted_stanza_id=payload.quoted_stanza_id,
            quoted_text=payload.quoted_text,
            quoted_phone=payload.quoted_no_pengurus(),
            push_name=payload.data.push_name,
        )


class CommandType(StrEnum):
    """WhatsApp dues bot supported commands."""

    ACC = "/acc"
    REJECT = "/reject"
    TUNAI = "/tunai"
    KOREKSI = "/koreksi"
    UNDO = "/undo"
    BAYAR = "/bayar"
    STATUS = "/status"
    HELP = "/help"
    CEK = "/cek"


def parse_amount(text: str) -> int | None:
    """Extract integer amount from string, stripping dots, commas, currency symbols, and cents."""
    if not text:
        return None
    # Strip decimal cents: ,00 (Indonesian) or .00 (English 2-digit cents)
    cleaned = re.sub(r"[,.]00(?=\s|$|[^\d])", "", text.strip())
    digits = re.sub(r"[^\d]", "", cleaned)
    return int(digits) if digits else None


def parse_acc_command(text: str) -> int | None:
    """Parse /acc [amount] command. Returns explicit amount if provided, else None."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        return parse_amount(parts[1])
    return None


def parse_reject_command(text: str) -> str:
    """Parse /reject [reason] command. Returns reason string."""
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def parse_phone_and_amount(text: str) -> tuple[str, int] | None:
    """Parse commands with phone and amount (/tunai <phone> <amt> or /setsaldo <phone> <amt>)."""
    parts = text.strip().split()
    if len(parts) >= 3:
        amount = parse_amount(parts[-1])
        phone_raw = "".join(parts[1:-1])
        phone = re.sub(r"\D", "", phone_raw)
        if phone.startswith("0"):
            phone = "62" + phone[1:]
        if phone and amount is not None:
            return (phone, amount)
    return None


def parse_command_type(text: str) -> CommandType | None:
    """Extract command type enum from message text if valid command."""
    cleaned = text.strip().lower()
    first_word = cleaned.split()[0] if cleaned else ""
    for cmd in CommandType:
        if first_word == cmd.value:
            return cmd
    return None
