"""Unit tests for configuration loader (src/wri_kas_bot/config.py)."""

import pytest
from pydantic import ValidationError

from wri_kas_bot.config import Settings, get_settings


def test_default_settings_instantiation():
    """Verify Settings initializes with zero-PII defaults."""
    settings = Settings()
    assert settings.due_amount_per_month == 10000
    assert settings.bot_instance_name in ("kas-wri", "kas-wri-test")
    assert settings.billing_hour == 19
    assert settings.billing_minute == 0
    assert "mock" in settings.evolution_api_key.lower() or len(settings.evolution_api_key) > 0
    assert "mock" in settings.gemini_api_key.lower() or len(settings.gemini_api_key) > 0
    assert settings.bendahara_number == "6281100000001"
    assert settings.bot_number == "6281100000002"


def test_settings_override_via_env(monkeypatch):
    """Verify Settings loads custom values via env aliases."""
    monkeypatch.setenv("BOT_INSTANCE_NAME", "custom-instance")
    monkeypatch.setenv("DUE_AMOUNT_PER_MONTH", "15000")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", "custom/db.sqlite")
    monkeypatch.setenv("SPREADSHEET_ID", "custom-sheet-id")
    monkeypatch.setenv("BENDAHARA_NUMBER", "6281100000099")

    settings = Settings()
    assert settings.bot_instance_name == "custom-instance"
    assert settings.due_amount_per_month == 15000
    assert settings.database_url == "custom/db.sqlite"
    assert settings.google_spreadsheet_id == "custom-sheet-id"
    assert settings.bendahara_number == "6281100000099"
    assert settings.bendahara_jid == "6281100000099@s.whatsapp.net"


def test_settings_derived_jids():
    """Verify derived bot_jid and bendahara_jid WhatsApp formatting."""
    settings = Settings()
    assert settings.bendahara_jid.endswith("@s.whatsapp.net")
    assert settings.bendahara_jid.split("@")[0].isdigit()
    assert settings.bot_jid.endswith("@s.whatsapp.net")
    assert settings.bot_jid.split("@")[0].isdigit()


def test_settings_database_path_property():
    """Verify database_path extracts clean filesystem path."""
    settings = Settings(database_url="sqlite+aiosqlite:///data/kas_wri.db")
    assert settings.database_path == "data/kas_wri.db"

    direct_settings = Settings(database_url="data/kas_wri.db")
    assert direct_settings.database_path == "data/kas_wri.db"


def test_settings_validation_errors():
    """Verify validation triggers on invalid phone numbers, negative fees, or bad schedules."""
    with pytest.raises(ValidationError):
        Settings(due_amount_per_month=0)

    with pytest.raises(ValidationError):
        Settings(due_amount_per_month=-5000)

    with pytest.raises(ValidationError):
        Settings(billing_hour=25)

    with pytest.raises(ValidationError):
        Settings(billing_minute=61)

    with pytest.raises(ValidationError):
        Settings(bendahara_number="non-numeric-phone")


def test_get_settings_caching():
    """Verify get_settings returns cached singleton."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
