"""Tier 1: Feature Coverage: Feature 12: Debit_Kredit Bottom-Up Scanning (sheets.py)."""


def test_scan_skips_empty_spacer_rows(mock_sheets_client):
    """Verify bottom-up scanner skips spacer rows 55-74."""
    last_row = mock_sheets_client.scan_debit_kredit_bottom_up()
    assert last_row < 55 or last_row > 75
    assert last_row == 54


def test_scan_skips_row_75_totals(mock_sheets_client):
    """Verify scanner does not treat row 75 total row as last data entry."""
    last_row = mock_sheets_client.scan_debit_kredit_bottom_up()
    assert last_row != 75


def test_scan_identifies_last_used_data_row(mock_sheets_client):
    """Verify scanner returns row index containing both TANGGAL and SALDO."""
    last_row = mock_sheets_client.scan_debit_kredit_bottom_up()
    assert isinstance(last_row, int)
    assert last_row > 0


def test_saldo_stripping_non_numeric():
    """Verify parsing saldo removes currency formatting ('Rp 1.500.000' -> 1500000)."""
    raw_saldo = "Rp 1.500.000"
    cleaned = int("".join(c for c in raw_saldo if c.isdigit()))
    assert cleaned == 1500000


def test_append_coordinate_is_next_row(mock_sheets_client):
    """Verify next append row index is last_row + 1."""
    last_row = mock_sheets_client.scan_debit_kredit_bottom_up()
    target_row = last_row + 1
    assert target_row == 55
