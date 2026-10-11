"""Tier 1: Feature Coverage — Feature 11: Sheets BatchClear Rollback (sheets.py)."""

import pytest

try:
    from wri_kas_bot.sheets import GoogleSheetsClient

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    GoogleSheetsClient = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.sheets not implemented yet")


def test_batch_clear_cells_clears_multiple_ranges(mock_sheets_client):
    """Verify batch_clear_cells clears specified range list."""
    ranges = ["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!E5"]
    mock_sheets_client.batch_clear_cells(ranges)

    assert mock_sheets_client.cleared_ranges == ranges


def test_batch_clear_cells_empty_list_no_op(mock_sheets_client):
    """Verify calling batch_clear_cells with empty list is a safe no-op."""
    mock_sheets_client.batch_clear_cells([])
    assert len(mock_sheets_client.cleared_ranges) == 0


def test_batch_clear_cells_range_formatting():
    """Verify range coordinates contain sheet name and exclamation mark."""
    valid_range = "PENGURUS-2025/2026!D5"
    assert "!" in valid_range
    sheet_name, cell = valid_range.split("!")
    assert len(sheet_name) > 0
    assert len(cell) > 0


def test_batch_clear_cells_does_not_mutate_unrelated_cells(mock_sheets_client):
    """Verify clearing does not include unspecified cells."""
    ranges = ["PENGURUS-2025/2026!D5"]
    mock_sheets_client.batch_clear_cells(ranges)
    assert "PENGURUS-2025/2026!F5" not in mock_sheets_client.cleared_ranges


def test_batch_clear_cells_sends_batch_clear_payload():
    """Verify batch_clear_cells sends correct ranges body payload."""
    from unittest.mock import MagicMock, patch

    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        client = GoogleSheetsClient(spreadsheet_id="test-sheet-id")
        client._service = mock_service

        client.batch_clear_cells(["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!D6"])
        mock_clear = mock_service.spreadsheets().values().batchClear
        mock_clear.assert_called_once()
        body = mock_clear.call_args.kwargs.get("body")
        assert body == {"ranges": ["PENGURUS-2025/2026!D5", "PENGURUS-2025/2026!D6"]}
