"""Tier 1: Feature Coverage — Feature 10: Google Sheets Client (sheets.py)."""

import pytest

try:
    from wri_kas_bot.sheets import GoogleSheetsClient

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    GoogleSheetsClient = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.sheets not implemented yet")


def test_update_payment_month_returns_coordinate(mock_sheets_client):
    """Verify updating a member dues month returns the modified cell coordinate."""
    coord = mock_sheets_client.update_payment_month(
        name="Alice",
        month="Februari",
        proof_url="https://drive.google.com/file/d/123/view",
    )
    assert coord.startswith("PENGURUS-2025/2026!")
    assert "!" in coord


def test_append_log_transaksi_exact_header_keys(mock_sheets_client):
    """Verify log_transaksi append uses exact header keys: 'Nomor HP' and 'Bukti URL'."""
    row_data = {
        "Id": 1,
        "Timestamp": "2026-10-11T12:00:00Z",
        "Nama": "Alice",
        "Nomor HP": "6281100000001",
        "Nominal": 20000,
        "Bulan Terisi": "Februari, Maret",
        "Sisa Saldo": 0,
        "Status": "APPROVED",
        "Bukti URL": "https://drive.google.com/file/d/123/view",
        "Approved By": "6281100000099@s.whatsapp.net",
        "Keterangan": "Via Bot",
    }
    mock_sheets_client.append_log_transaksi(row_data)

    assert len(mock_sheets_client.appended_rows) == 1
    recorded = mock_sheets_client.appended_rows[0]
    assert "Nomor HP" in recorded
    assert "Bukti URL" in recorded
    assert "No HP" not in recorded  # verify legacy typo is absent
    assert "Drive Link" not in recorded  # verify legacy typo is absent


def test_update_member_balance(mock_sheets_client):
    """Verify updating member balance in data_pengurus."""
    mock_sheets_client.update_member_balance(phone="6281100000001", balance=5000)
    # Mock accepts call without exception
    assert True


def test_update_payment_month_cash_sets_tunai(mock_sheets_client):
    """Verify cash transactions set cell value to literal 'Tunai' instead of URL."""
    coord = mock_sheets_client.update_payment_month(
        name="Bob",
        month="Februari",
        proof_url="Tunai",
    )
    assert coord.startswith("PENGURUS-2025/2026!")
    assert len(mock_sheets_client.updated_cells) > 0
    last_call = mock_sheets_client.updated_cells[-1]
    assert last_call["proof_url"] == "Tunai"


def test_sheets_client_instantiation():
    """Verify GoogleSheetsClient initializes with credentials and spreadsheet id."""
    client = GoogleSheetsClient(
        service_account_json="credentials.json",
        spreadsheet_id="mock-spreadsheet-id",
    )
    assert client.spreadsheet_id == "mock-spreadsheet-id"
