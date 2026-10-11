"""Unit tests for deterministic FIFO dues allocation engine (src/wri_kas_bot/fifo.py)."""

import pytest

from wri_kas_bot.fifo import allocate_dues
from wri_kas_bot.repository import MonthlyDue

ALL_UNPAID_2026 = [(2026, m) for m in range(2, 13)]  # Feb to Dec (11 months)


def test_case_1_exact_payment_one_month():
    """Case 1: Rp 10.000 exact payment for 1 month."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=10000,
        current_balance=0,
    )
    assert res.balance_before == 0
    assert res.balance_after == 0
    assert res.new_deposit == 0
    assert res.total_used == 10000
    assert res.months_filled == [(2026, 2)]
    assert res.cleared_month_names == ["Februari"]


def test_case_2_exact_payment_multi_month():
    """Case 2: Rp 30.000 exact payment for 3 months."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=30000,
        current_balance=0,
    )
    assert res.balance_before == 0
    assert res.balance_after == 0
    assert res.new_deposit == 0
    assert res.total_used == 30000
    assert res.months_filled == [(2026, 2), (2026, 3), (2026, 4)]
    assert res.cleared_month_names == ["Februari", "Maret", "April"]


def test_case_3_underpayment_accumulates_deposit():
    """Case 3: Rp 5.000 underpayment -> 0 months filled, deposit balance Rp 5.000."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=5000,
        current_balance=0,
    )
    assert res.balance_before == 0
    assert res.balance_after == 5000
    assert res.new_deposit == 5000
    assert res.total_used == 0
    assert res.months_filled == []
    assert res.cleared_month_names == []


def test_case_4_subsequent_installment_completes_month():
    """Case 4: Prior deposit Rp 5.000 + new payment Rp 5.000 -> 1 month filled, balance Rp 0."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=5000,
        current_balance=5000,
    )
    assert res.balance_before == 5000
    assert res.balance_after == 0
    assert res.new_deposit == 0
    assert res.total_used == 10000
    assert res.months_filled == [(2026, 2)]
    assert res.cleared_month_names == ["Februari"]


def test_case_5_odd_payment_surplus():
    """Case 5: Odd payment Rp 25.000 -> 2 months filled, deposit balance Rp 5.000."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=25000,
        current_balance=0,
    )
    assert res.balance_before == 0
    assert res.balance_after == 5000
    assert res.new_deposit == 5000
    assert res.total_used == 20000
    assert res.months_filled == [(2026, 2), (2026, 3)]
    assert res.cleared_month_names == ["Februari", "Maret"]


def test_case_6_advance_payment_future_months():
    """Case 6: Advance payment when past dues (Feb-Apr) are already paid."""
    future_unpaid = [(2026, m) for m in range(5, 13)]
    res = allocate_dues(
        unpaid_months=future_unpaid,
        amount_paid=50000,
        current_balance=0,
    )
    assert res.balance_before == 0
    assert res.balance_after == 0
    assert res.total_used == 50000
    assert res.months_filled == [
        (2026, 5),
        (2026, 6),
        (2026, 7),
        (2026, 8),
        (2026, 9),
    ]
    assert res.cleared_month_names == ["Mei", "Juni", "Juli", "Agustus", "September"]


def test_case_7_high_advance_payment_exceeding_queue():
    """Case 7: High advance payment (Rp 150.000 for 11 unpaid months)."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=150000,
        current_balance=0,
    )
    assert len(res.months_filled) == 11
    assert res.months_filled == ALL_UNPAID_2026
    assert res.total_used == 110000
    assert res.balance_after == 40000
    assert res.new_deposit == 40000


def test_payment_with_empty_unpaid_queue():
    """Boundary: Member is fully paid up, pays Rp 50.000."""
    res = allocate_dues(unpaid_months=[], amount_paid=50000, current_balance=10000)
    assert res.months_filled == []
    assert res.total_used == 0
    assert res.balance_before == 10000
    assert res.balance_after == 60000


def test_zero_amount_payment():
    """Boundary: Zero payment nominal."""
    res = allocate_dues(ALL_UNPAID_2026, amount_paid=0, current_balance=5000)
    assert res.months_filled == []
    assert res.balance_after == 5000


def test_negative_or_invalid_inputs_raise():
    """Boundary: Negative amount or non-positive dues fee raises ValueError."""
    with pytest.raises(ValueError, match="negative"):
        allocate_dues(ALL_UNPAID_2026, amount_paid=-10000)

    with pytest.raises(ValueError, match="negative"):
        allocate_dues(ALL_UNPAID_2026, amount_paid=10000, current_balance=-5000)

    with pytest.raises(ValueError, match="greater than 0"):
        allocate_dues(ALL_UNPAID_2026, amount_paid=10000, due_per_month=0)


def test_custom_due_amount():
    """Boundary: Custom due fee per month (e.g. Rp 15.000)."""
    res = allocate_dues(
        unpaid_months=ALL_UNPAID_2026,
        amount_paid=35000,
        current_balance=0,
        due_per_month=15000,
    )
    assert len(res.months_filled) == 2
    assert res.total_used == 30000
    assert res.balance_after == 5000


def test_input_formats_dicts_and_objects():
    """Verify allocate_dues accepts dictionaries and MonthlyDue objects."""
    dict_unpaid = [
        {"year": 2026, "month_index": 2, "month": "Februari"},
        {"year": 2026, "month_index": 3, "month": "Maret"},
    ]
    res_dict = allocate_dues(dict_unpaid, 10000)
    assert res_dict.months_filled == [(2026, 2)]
    assert res_dict.cleared_month_names == ["Februari"]

    obj_unpaid = [
        MonthlyDue(id=1, member_id=1, year=2026, month="Februari", month_index=2),
        MonthlyDue(id=2, member_id=1, year=2026, month="Maret", month_index=3),
    ]
    res_obj = allocate_dues(obj_unpaid, 20000)
    assert res_obj.months_filled == [(2026, 2), (2026, 3)]
    assert res_obj.cleared_month_names == ["Februari", "Maret"]


@pytest.mark.parametrize(
    ("b_before", "p", "unpaid_count"),
    [
        (0, 10000, 11),
        (5000, 15000, 11),
        (3000, 7500, 5),
        (12000, 3000, 2),
        (0, 120000, 11),
        (50000, 0, 11),
        (1000, 9000, 1),
    ],
)
def test_conservation_of_money_property(b_before, p, unpaid_count):
    """Property test: Total funds = Total Consumed + Ending Balance."""
    unpaid = [(2026, i + 2) for i in range(unpaid_count)]
    fee = 10000
    res = allocate_dues(unpaid, amount_paid=p, current_balance=b_before, due_per_month=fee)

    assert b_before + p == (len(res.months_filled) * fee) + res.balance_after
    assert res.balance_after == res.new_deposit


def test_fifo_january_month_index_mapping():
    """Verify month_index=1 maps to 'Januari' instead of fallback 'Bulan-1'."""
    unpaid = [(2026, 1), (2026, 2)]
    res = allocate_dues(unpaid, amount_paid=10000)
    assert res.months_filled == [(2026, 1)]
    assert res.cleared_month_names == ["Januari"]


def test_fifo_float_amount_paid_and_balance():
    """Verify float inputs (e.g. 10000.0 from JSON/OCR) do not crash list slicing."""
    unpaid = [(2026, 2), (2026, 3)]
    res = allocate_dues(unpaid, amount_paid=10000.0, current_balance=0.0)
    assert res.months_filled == [(2026, 2)]
    assert res.balance_after == 0
    assert isinstance(res.balance_after, int)
    assert isinstance(res.total_used, int)
    assert isinstance(res.amount_paid, int)
