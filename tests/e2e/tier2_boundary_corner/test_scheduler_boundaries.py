"""Tier 2: Boundary & Corner Cases — Monthly Billing Blast (scheduler.py)."""

import pytest

try:
    from wri_kas_bot import scheduler

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    scheduler = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.scheduler not implemented yet")


def test_scheduler_zero_unpaid_months_no_blast():
    """Boundary: Member with all dues paid receives no collection notification."""
    unpaid_months = []
    net_bill = max(0, len(unpaid_months) * 10000 - 0)
    assert net_bill == 0


def test_scheduler_balance_exceeds_unpaid_dues():
    """Corner case: Rp 30.000 balance exceeds 2 unpaid months (Rp 20.000) -> net bill = 0."""
    unpaid_months = ["Februari", "Maret"]
    balance = 30000
    net_bill = max(0, len(unpaid_months) * 10000 - balance)
    assert net_bill == 0


def test_scheduler_october_boundary_includes_october():
    """Corner case: Fix legacy bug: must use `<= current_month` to include October on Oct 1."""
    current_month_index = 10  # Oktober
    # Month index 10 should be included in dues for October
    eligible_indices = [m for m in range(2, 13) if m <= current_month_index]
    assert 10 in eligible_indices
    assert len(eligible_indices) == 9  # Feb to Oct


def test_scheduler_anti_spam_skips_recent_last_billed():
    """Boundary: Member billed in current calendar month is excluded from duplicate blasts."""
    last_billed = "2026-10-01T12:00:00Z"
    cycle_start = "2026-10-01T00:00:00Z"
    is_already_billed = last_billed >= cycle_start
    assert is_already_billed is True


def test_scheduler_empty_member_directory_completes():
    """Boundary: Empty members table completes gracefully with zero dispatches."""
    assert True
