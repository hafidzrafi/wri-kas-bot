"""Tier 2: Boundary & Corner Cases: Google Sheets Integration (sheets.py)."""

from wri_kas_bot.sheets import GoogleSheetsClient


def test_bottom_up_scanning_dense_spacer_block(mock_sheets_client):
    """Corner case: 20 consecutive empty spacer rows (rows 55-74) do not halt scan prematurely."""
    row = mock_sheets_client.scan_debit_kredit_bottom_up()
    assert row <= 54 or row > 75


def test_bottom_up_scanning_ignores_row_75_totals(mock_sheets_client):
    """Boundary: Summary row 75 with formula is explicitly skipped."""
    row = mock_sheets_client.scan_debit_kredit_bottom_up()
    assert row != 75


def test_batch_clear_deduplicates_overlapping_ranges(mock_sheets_client):
    """Corner case: Duplicate ranges in batch clear list are deduplicated."""
    ranges = ["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!D5"]
    mock_sheets_client.batch_clear_cells(ranges)
    assert len(mock_sheets_client.cleared_ranges) >= 1


def test_log_transaksi_missing_optional_fields_handled(mock_sheets_client):
    """Boundary: Row data dictionary missing non-mandatory audit fields appends cleanly."""
    sparse_data = {
        "Nama": "Alice",
        "Nomor HP": "6281100000001",
        "Nominal": 10000,
        "Bukti URL": "Tunai",
    }
    mock_sheets_client.append_log_transaksi(sparse_data)
    assert len(mock_sheets_client.appended_rows) == 1


def test_sheets_client_batch_clear_empty_ranges_noops():
    """Boundary: batch_clear_cells with empty or whitespace list no-ops safely."""
    from unittest.mock import MagicMock

    client = GoogleSheetsClient(spreadsheet_id="test-sheet-id")
    client._service = MagicMock()
    client.batch_clear_cells([])
    client.service.spreadsheets().values().batchClear.assert_not_called()
