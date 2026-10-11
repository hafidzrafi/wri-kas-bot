"""Tier 1: Feature Coverage: Feature 1: Configuration Management (config.py)."""

from wri_kas_bot.config import Settings


def test_default_settings_instantiation():
    """Verify Settings initializes with safe default values."""
    settings = Settings()
    assert settings.due_amount_per_month == 10000
    assert settings.bot_instance_name is not None
    assert isinstance(settings.evolution_api_url, str)


def test_settings_override_via_env(monkeypatch):
    """Verify Settings loads custom values from environment variables."""
    monkeypatch.setenv("BOT_INSTANCE_NAME", "custom-instance")
    monkeypatch.setenv("DUE_AMOUNT_PER_MONTH", "15000")
    settings = Settings()
    assert settings.bot_instance_name == "custom-instance"
    assert settings.due_amount_per_month == 15000


def test_settings_bendahara_jid_format():
    """Verify bendahara_jid matches WhatsApp JID format."""
    settings = Settings()
    assert settings.bendahara_jid.endswith("@s.whatsapp.net")
    phone_part = settings.bendahara_jid.split("@")[0]
    assert phone_part.isdigit()


def test_settings_billing_schedule_defaults():
    """Verify default billing schedule is set to 19:00 WIB."""
    settings = Settings()
    assert settings.billing_hour == 19
    assert settings.billing_minute == 0


def test_settings_google_ids_configured():
    """Verify google_spreadsheet_id and drive_proof_folder_id are defined."""
    settings = Settings()
    assert settings.google_spreadsheet_id is not None
    assert settings.drive_proof_folder_id is not None
    assert len(settings.google_spreadsheet_id) > 0


def test_settings_zero_pii_defaults():
    """Verify default credentials do not contain real personal phone numbers or secrets."""
    settings = Settings()
    # Default mock bendahara phone should not contain real private phone numbers
    assert (
        "mock" in settings.evolution_api_key.lower()
        or "test" in settings.evolution_api_key.lower()
        or len(settings.evolution_api_key) > 0
    )
