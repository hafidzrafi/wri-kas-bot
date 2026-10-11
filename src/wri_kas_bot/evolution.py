"""Evolution API v2 HTTP client for WhatsApp automation.

Provides async text message dispatch, Base64 Data URI media forwarding,
and JID normalization for Evolution API v2 gateway.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from types import TracebackType
from typing import Any, Self

import httpx

from wri_kas_bot.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EvolutionAPIError(RuntimeError):
    """Raised when an Evolution API request fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: Any = None,
        request_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.request_url = request_url


def normalize_jid(recipient: str) -> str:
    """Normalize phone number or JID to canonical WhatsApp JID format."""
    clean = recipient.strip()
    if clean.endswith("@g.us"):
        prefix = clean[:-5]
        return f"{prefix.strip()}@g.us"
    prefix = clean[:-15] if clean.endswith("@s.whatsapp.net") else clean
    digits = re.sub(r"\D", "", prefix)
    if not digits:
        raise ValueError(f"Invalid recipient phone number: '{recipient}'")
    return f"{digits}@s.whatsapp.net"


class EvolutionClient:
    """Async HTTP client for Evolution API v2 WhatsApp gateway."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        instance: str | None = None,
        settings: Settings | None = None,
        timeout: float = 30.0,
    ) -> None:
        cfg = settings or get_settings()
        self.base_url = (base_url or cfg.evolution_api_url).rstrip("/")
        self.api_key = api_key or cfg.evolution_api_key
        self.instance = instance or cfg.bot_instance_name
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def headers(self) -> dict[str, str]:
        """HTTP headers for Evolution API authentication."""
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or lazily create internal httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close internal httpx client pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        await self._get_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _extract_message_id(self, response_data: Any) -> str:
        """Extract WhatsApp message ID from Evolution API response payload."""
        msg_id = None
        if isinstance(response_data, dict):
            key = response_data.get("key")
            if isinstance(key, dict):
                msg_id = key.get("id")
            if not msg_id:
                msg_id = response_data.get("id") or response_data.get("messageId")
        elif isinstance(response_data, list) and response_data:
            first = response_data[0]
            if isinstance(first, dict):
                msg_id = first.get("key", {}).get("id") or first.get("id")

        if not msg_id:
            msg_id = "UNKNOWN_MSG_ID"
        return str(msg_id)

    async def send_text(self, jid: str, text: str) -> str:
        """Send plain text message to WhatsApp recipient."""
        formatted_jid = normalize_jid(jid)
        encoded_instance = urllib.parse.quote(self.instance, safe="")
        url = f"{self.base_url}/message/sendText/{encoded_instance}"
        payload = {
            "number": formatted_jid,
            "text": text,
        }

        client = await self._get_client()
        try:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code not in (200, 201):
                raise EvolutionAPIError(
                    f"Evolution API sendText failed ({resp.status_code}): {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    request_url=url,
                )
            data = resp.json()
            msg_id = self._extract_message_id(data)
            logger.info("Sent text to %s (msg_id: %s)", formatted_jid, msg_id)
            return msg_id
        except httpx.HTTPError as exc:
            raise EvolutionAPIError(
                f"HTTP transport failure calling sendText: {exc}",
                request_url=url,
            ) from exc

    async def send_image_base64(
        self,
        jid: str,
        image_base64: str,
        caption: str = "",
    ) -> str:
        """Send base64-encoded image to WhatsApp recipient using Data URI scheme."""
        formatted_jid = normalize_jid(jid)
        clean_b64 = image_base64.strip()
        if clean_b64.startswith("data:"):
            media_data_uri = clean_b64
        else:
            media_data_uri = f"data:image/jpeg;base64,{clean_b64}"

        encoded_instance = urllib.parse.quote(self.instance, safe="")
        url = f"{self.base_url}/message/sendMedia/{encoded_instance}"
        payload = {
            "number": formatted_jid,
            "mediatype": "image",
            "mimetype": "image/jpeg",
            "caption": caption,
            "media": media_data_uri,
            "fileName": "receipt.jpg",
        }

        client = await self._get_client()
        try:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code not in (200, 201):
                raise EvolutionAPIError(
                    f"Evolution API sendMedia failed ({resp.status_code}): {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    request_url=url,
                )
            data = resp.json()
            msg_id = self._extract_message_id(data)
            logger.info("Sent image to %s (msg_id: %s)", formatted_jid, msg_id)
            return msg_id
        except httpx.HTTPError as exc:
            raise EvolutionAPIError(
                f"HTTP transport failure calling sendMedia: {exc}",
                request_url=url,
            ) from exc

    async def get_base64_from_media_message(
        self,
        key: dict[str, Any],
        message: dict[str, Any],
    ) -> str:
        """Fetch media base64 from Evolution API if not present in inbound webhook."""
        encoded_instance = urllib.parse.quote(self.instance, safe="")
        url = f"{self.base_url}/chat/getBase64FromMediaMessage/{encoded_instance}"
        payload = {
            "message": {
                "key": key,
                "message": message,
            }
        }
        client = await self._get_client()
        try:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code not in (200, 201):
                raise EvolutionAPIError(
                    f"Evolution API getBase64 failed ({resp.status_code}): {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    request_url=url,
                )
            data = resp.json()
            raw_b64 = data.get("base64", "")
            if "," in raw_b64:
                raw_b64 = raw_b64.split(",", 1)[1]
            return str(raw_b64)
        except httpx.HTTPError as exc:
            raise EvolutionAPIError(
                f"HTTP transport failure calling getBase64FromMediaMessage: {exc}",
                request_url=url,
            ) from exc
