"""Tier 1: Feature Coverage: Feature 4: Deterministic FIFO Engine (fifo.py)."""

from wri_kas_bot.fifo import allocate_dues

ALL_UNPAID_2026 = [(2026, m) for m in range(2, 13)]  # Feb to Dec (11 months)


def test_fifo_exact_single_month():
    """Verify single exact payment (Rp 10.000) clears the first chronological month."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=10000, current_balance=0)
    assert len(res.months_filled) == 1
    assert res.months_filled[0] == (2026, 2)
    assert res.balance_before == 0
    assert res.balance_after == 0
    assert res.new_deposit == 0


def test_fifo_exact_multi_month():
    """Verify multi-month payment (Rp 30.000) clears 3 chronological months."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=30000, current_balance=0)
    assert len(res.months_filled) == 3
    assert res.months_filled == [(2026, 2), (2026, 3), (2026, 4)]
    assert res.balance_after == 0


def test_fifo_partial_underpayment():
    """Verify underpayment (Rp 5.000) clears 0 months and accumulates into balance."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=5000, current_balance=0)
    assert len(res.months_filled) == 0
    assert res.balance_before == 0
    assert res.balance_after == 5000
    assert res.new_deposit == 5000


def test_fifo_accumulated_deposit_trigger():
    """Verify existing deposit combined with payment reaches due threshold to clear month."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=5000, current_balance=5000)
    assert len(res.months_filled) == 1
    assert res.months_filled[0] == (2026, 2)
    assert res.balance_before == 5000
    assert res.balance_after == 0


def test_fifo_odd_amount_leftover():
    """Verify odd amount (Rp 25.000) clears 2 months with Rp 5.000 remainder in balance."""
    res = allocate_dues(unpaid_months=ALL_UNPAID_2026, amount_paid=25000, current_balance=0)
    assert len(res.months_filled) == 2
    assert res.months_filled == [(2026, 2), (2026, 3)]
    assert res.balance_after == 5000
    assert res.new_deposit == 5000


def test_fifo_excess_payment_past_december():
    """Verify advance payment when unpaid months are fewer than paid amount."""
    short_unpaid = [(2026, 11), (2026, 12)]  # Nov, Dec
    res = allocate_dues(unpaid_months=short_unpaid, amount_paid=50000, current_balance=0)
    assert len(res.months_filled) == 2
    assert res.months_filled == [(2026, 11), (2026, 12)]
    # 50000 - 20000 = 30000 remains in balance
    assert res.balance_after == 30000
