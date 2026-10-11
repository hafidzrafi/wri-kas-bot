"""Configuration management module for wri-kas-bot.

Provides strict environment variable loading and validation using Pydantic Settings
with zero-PII defaults.
"""

import re
from functools import lru_cache
from typing import Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # 1. Database & Storage Configuration
    database_url: str = Field(
        default="sqlite:///data/kas_wri.db",
        validation_alias=AliasChoices(
            "DATABASE_URL", "DATABASE_PATH", "database_url", "database_path"
        ),
        description="SQLite database path or URL. Defaults to local data/kas_wri.db.",
    )
    receipts_dir: str = Field(
        default="data/receipts",
        validation_alias=AliasChoices("RECEIPTS_DIR", "receipts_dir"),
        description="Local directory for temporary storage of downloaded receipt images.",
    )

    # 2. Evolution API (WhatsApp Gateway)
    evolution_api_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices("EVOLUTION_API_URL", "evolution_api_url"),
        description="Evolution API v2 gateway base URL.",
    )
    evolution_api_key: str = Field(
        default="mock-key",
        validation_alias=AliasChoices("EVOLUTION_API_KEY", "evolution_api_key"),
        description="Global API key for Evolution API authentication.",
    )
    bot_instance_name: str = Field(
        default="kas-wri",
        validation_alias=AliasChoices("BOT_INSTANCE_NAME", "bot_instance_name"),
        description="Instance name configured in Evolution API.",
    )

    # 3. WhatsApp Numbers & JIDs
    bendahara_number: str = Field(
        default="6281100000099",
        validation_alias=AliasChoices("BENDAHARA_NUMBER", "bendahara_number"),
        description="Digits-only phone number of the active treasurer.",
    )
    bot_number: str = Field(
        default="6281100000002",
        validation_alias=AliasChoices("BOT_NUMBER", "bot_number"),
        description="Digits-only phone number of the bot WhatsApp account.",
    )
    bendahara_jid: str = Field(
        default="6281100000099@s.whatsapp.net",
        validation_alias=AliasChoices("BENDAHARA_JID", "bendahara_jid"),
        description="WhatsApp JID of active treasurer.",
    )

    # 4. Google Workspace Integration
    google_spreadsheet_id: str = Field(
        default="mock-spreadsheet-id",
        validation_alias=AliasChoices(
            "SPREADSHEET_ID", "GOOGLE_SPREADSHEET_ID", "google_spreadsheet_id"
        ),
        description="Google Sheets spreadsheet ID for dues management.",
    )
    drive_proof_folder_id: str = Field(
        default="mock-drive-folder-id",
        validation_alias=AliasChoices("DRIVE_PROOF_FOLDER_ID", "drive_proof_folder_id"),
        description="Google Drive destination folder ID for archival of receipt images.",
    )
    google_service_account_json: str = Field(
        default="credentials.json",
        validation_alias=AliasChoices("GOOGLE_SERVICE_ACCOUNT_JSON", "google_service_account_json"),
        description="Path to Google Service Account credentials JSON file.",
    )
    google_drive_token_json: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_DRIVE_TOKEN_JSON", "google_drive_token_json"),
        description="Optional OAuth token path for user-delegated Drive access.",
    )
    qris_file_url: str = Field(
        default="https://example.com/qris.jpg",
        validation_alias=AliasChoices("QRIS_FILE_URL", "qris_file_url"),
        description="Public URL to organization QRIS payment image.",
    )
    bank_account_number: str = Field(
        default="1000 0000 0001",
        validation_alias=AliasChoices("BANK_ACCOUNT_NUMBER", "bank_account_number"),
        description="Bank account number displayed for dues payments.",
    )
    bank_account_holder: str = Field(
        default="Kas WRI / Bendahara",
        validation_alias=AliasChoices("BANK_ACCOUNT_HOLDER", "bank_account_holder"),
        description="Bank account holder name displayed for dues payments.",
    )

    # 5. Multimodal Vision AI
    gemini_api_key: str = Field(
        default="mock-gemini-key",
        validation_alias=AliasChoices("GEMINI_API_KEY", "gemini_api_key"),
        description="Google Gemini Flash API key for receipt OCR extraction.",
    )
    gemini_model: str = Field(
        default="gemini-1.5-flash",
        validation_alias=AliasChoices("GEMINI_MODEL", "gemini_model"),
        description="Google Gemini model identifier for multimodal vision receipt extraction.",
    )

    # 6. Dues Policy & Automation Scheduling
    due_amount_per_month: int = Field(
        default=10000,
        validation_alias=AliasChoices("DUE_AMOUNT_PER_MONTH", "due_amount_per_month"),
        description="Monthly dues fee in IDR per member.",
    )
    billing_hour: int = Field(
        default=19,
        validation_alias=AliasChoices("BILLING_HOUR", "billing_hour"),
        description="WIB hour (0-23) for monthly billing reminder blast.",
    )
    billing_minute: int = Field(
        default=0,
        validation_alias=AliasChoices("BILLING_MINUTE", "billing_minute"),
        description="Minute (0-59) for monthly billing reminder blast.",
    )

    # 7. Alerting & Security
    telegram_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
        description="Optional Telegram bot token for operational failure alerts.",
    )
    telegram_admin_chat_id: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_ADMIN_CHAT_ID", "telegram_admin_chat_id"),
        description="Optional Telegram admin chat ID for alerting.",
    )
    webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WEBHOOK_SECRET", "webhook_secret"),
        description="Optional shared secret for inbound webhook validation.",
    )
    port: int = Field(
        default=8090,
        validation_alias=AliasChoices("PORT", "port"),
        description="Server port. Defaults to 8090 to match Evolution API webhook.",
    )
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("HOST", "host"),
        description="Server listening host address. Defaults to 0.0.0.0.",
    )

    # 8. WhatsApp Interaction Experience
    typing_delay_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("TYPING_DELAY_ENABLED", "typing_delay_enabled"),
        description="Enable presence composing and typing delay before replying.",
    )
    typing_delay_base_ms: int = Field(
        default=1200,
        validation_alias=AliasChoices("TYPING_DELAY_BASE_MS", "typing_delay_base_ms"),
        description="Base delay in milliseconds for typing indicator.",
    )

    @property
    def database_path(self) -> str:
        """Extract clean filesystem path from database_url or return direct path."""
        url = self.database_url
        if ":///" in url:
            return url.split(":///", 1)[1]
        elif "://" in url:
            return url.split("://", 1)[1]
        return url

    @property
    def bot_jid(self) -> str:
        """Return bot WhatsApp JID."""
        clean_number = re.sub(r"[^0-9]", "", self.bot_number)
        return f"{clean_number}@s.whatsapp.net"

    @model_validator(mode="after")
    def _sync_bendahara_jid(self) -> Self:
        """Keep bendahara_jid in sync with bendahara_number if jid wasn't custom set."""
        digits = re.sub(r"[^0-9]", "", self.bendahara_number)
        if (
            digits
            and self.bendahara_jid == "6281100000001@s.whatsapp.net"
            and digits != "6281100000001"
        ):
            self.bendahara_jid = f"{digits}@s.whatsapp.net"
        return self

    @field_validator("bendahara_number", "bot_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"[^0-9]", "", v)
        if not digits:
            raise ValueError("Phone number must contain numeric digits")
        return digits

    @field_validator("due_amount_per_month")
    @classmethod
    def validate_positive_fee(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("due_amount_per_month must be greater than 0")
        return v

    @field_validator("billing_hour")
    @classmethod
    def validate_hour(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("billing_hour must be between 0 and 23")
        return v

    @field_validator("billing_minute")
    @classmethod
    def validate_minute(cls, v: int) -> int:
        if not (0 <= v <= 59):
            raise ValueError("billing_minute must be between 0 and 59")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of Settings."""
    return Settings()
