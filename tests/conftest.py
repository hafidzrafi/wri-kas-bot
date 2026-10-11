"""Root pytest configuration and shared fixtures for wri-kas-bot test suite."""

import os
import sqlite3
from collections.abc import Generator

import pytest

# Ensure safe testing environment variables
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/kas_wri.db")
os.environ.setdefault("EVOLUTION_API_URL", "http://localhost:8080")
os.environ.setdefault("EVOLUTION_API_KEY", "mock-test-key")
os.environ.setdefault("BOT_INSTANCE_NAME", "kas-wri-test")
os.environ.setdefault("GEMINI_API_KEY", "mock-gemini-key")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")
os.environ.setdefault("GOOGLE_SPREADSHEET_ID", "mock-spreadsheet-id")
os.environ.setdefault("DRIVE_PROOF_FOLDER_ID", "mock-drive-folder-id")
os.environ.setdefault("BENDAHARA_JID", "6281100000099@s.whatsapp.net")
os.environ.setdefault("DUE_AMOUNT_PER_MONTH", "10000")
os.environ.setdefault("BILLING_HOUR", "19")
os.environ.setdefault("BILLING_MINUTE", "0")
os.environ["TYPING_DELAY_ENABLED"] = "false"

# Synthetic Zero-PII Test Constants
MOCK_TREASURER_PHONE = "6281100000001"
MOCK_BOT_PHONE = "6281100000002"
MOCK_MEMBER_ALICE_PHONE = "6281100000003"
MOCK_MEMBER_BOB_PHONE = "6281100000004"


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Ensure cached settings singleton is reset between tests."""
    from wri_kas_bot.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def in_memory_db() -> Generator[sqlite3.Connection, None, None]:
    """Isolated in-memory SQLite connection with schema initialized and foreign keys enabled."""
    from wri_kas_bot.database import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_SQL)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def seeded_db(in_memory_db: sqlite3.Connection) -> sqlite3.Connection:
    """Database pre-seeded with Alice (ID 1, balance 0) and Bob (ID 2, balance 5000)."""
    from wri_kas_bot.repository import MemberRepository, MonthlyDuesRepository

    m_repo = MemberRepository(in_memory_db)
    d_repo = MonthlyDuesRepository(in_memory_db)

    # Seed Alice (ID 1)
    alice_id = m_repo.create(name="Alice", phone_number=MOCK_MEMBER_ALICE_PHONE, balance=0)
    d_repo.init_member_dues(alice_id, 2026)

    # Seed Bob (ID 2)
    bob_id = m_repo.create(name="Bob", phone_number=MOCK_MEMBER_BOB_PHONE, balance=5000)
    d_repo.init_member_dues(bob_id, 2026)

    in_memory_db.commit()
    return in_memory_db
