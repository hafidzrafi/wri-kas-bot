"""Tier 2: Boundary & Corner Cases — FIFO Allocation Engine (fifo.py)."""

import pytest

try:
    from wri_kas_bot.fifo import AllocationResult, allocate_dues

    HAS_MODULE = True
except ImportError:
    HAS_MODULE = False
    allocate_dues = None
    AllocationResult = None

pytestmark = pytest.mark.skipif(not HAS_MODULE, reason="wri_kas_bot.fifo not implemented yet")

ALL_UNPAID_2026 = [(2026, m) for m in range(2, 13)]


def test_fifo_zero_payment():
    """Boundary: Zero payment (amount_paid=0) leaves months filled and balance unchanged."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=0, current_balance=0)
    assert len(res.months_filled) == 0
    assert res.balance_after == 0
    assert res.new_deposit == 0


def test_fifo_negative_payment_raises_or_zeroes():
    """Boundary: Negative amount paid should raise ValueError or handle cleanly."""
    try:
        res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=-10000, current_balance=0)
        assert len(res.months_filled) == 0
    except ValueError:
        pass  # Raising ValueError on negative payment is valid behavior


def test_fifo_fractional_underpayment_5000():
    """Corner case: Underpayment of Rp 5.000 (below single month 10.000) becomes deposit."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=5000, current_balance=0)
    assert len(res.months_filled) == 0
    assert res.balance_after == 5000
    assert res.new_deposit == 5000


def test_fifo_odd_amount_15000():
    """Corner case: Payment of Rp 15.000 clears 1 month (Rp 10.000) and saves Rp 5.000 remainder."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=15000, current_balance=0)
    assert len(res.months_filled) == 1
    assert res.months_filled[0] == (2026, 2)
    assert res.balance_after == 5000
    assert res.new_deposit == 5000


def test_fifo_odd_amount_23500():
    """Corner case: Rp 23.500 payment clears 2 months (Rp 20.000) and saves Rp 3.500 remainder."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=23500, current_balance=0)
    assert len(res.months_filled) == 2
    assert res.months_filled == [(2026, 2), (2026, 3)]
    assert res.balance_after == 3500
    assert res.new_deposit == 3500


def test_fifo_accumulated_deposit_reaches_multiple():
    """Corner case: Rp 5.000 balance + Rp 15.000 payment clears exactly 2 months."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=15000, current_balance=5000)
    assert len(res.months_filled) == 2
    assert res.months_filled == [(2026, 2), (2026, 3)]
    assert res.balance_after == 0


def test_fifo_advance_payment_when_all_months_paid():
    """Corner case: When unpaid_months is empty, entire amount stays as advance treasury credit."""
    res = allocate_dues(unpaid_months=[], amount_paid=50000, current_balance=0)
    assert len(res.months_filled) == 0
    assert res.balance_after == 50000


def test_fifo_exact_full_year_payment_110000():
    """Boundary: Full year dues Rp 110.000 clears all 11 months with zero leftover balance."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=110000, current_balance=0)
    assert len(res.months_filled) == 11
    assert res.balance_after == 0
