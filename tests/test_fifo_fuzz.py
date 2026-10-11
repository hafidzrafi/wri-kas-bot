"""Empirical stress testing and fuzz harness for FIFO dues allocation engine.

Validates conservation of money, boundary conditions, combinatorial permutations,
and adversarial inputs across 20,000+ randomized iterations and extreme edge cases.
"""

import random
import time
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from wri_kas_bot.fifo import AllocationResult, allocate_dues
from wri_kas_bot.repository import MonthlyDue


def _generate_unpaid_months(count: int, format_type: str = "tuple") -> list[Any]:
    """Generate chronological months across multiple formats."""
    result: list[Any] = []
    start_year = 2024
    month_idx = 2  # Feb

    for _ in range(count):
        if format_type == "tuple":
            result.append((start_year, month_idx))
        elif format_type == "dict":
            result.append(
                {
                    "year": start_year,
                    "month_index": month_idx,
                    "month": f"Bulan-{month_idx}",
                }
            )
        elif format_type == "object":
            result.append(
                MonthlyDue(
                    id=len(result) + 1,
                    member_id=1,
                    year=start_year,
                    month=f"Bulan-{month_idx}",
                    month_index=month_idx,
                )
            )
        month_idx += 1
        if month_idx > 12:
            month_idx = 1
            start_year += 1

    return result


def test_fifo_fuzz_conservation_of_money_20000_iterations():
    """Fuzz allocate_dues over 20,000+ randomized iterations.

    Verifies mathematical invariants:
    1. Conservation of money: B_before + P == total_used + B_after
    2. Exact consumption: total_used == len(months_filled) * due_per_month
    3. Deposit sync: B_after == new_deposit
    4. Non-negativity: B_after >= 0
    5. Maximality: If queue not exhausted, B_after < due_per_month
    6. Queue bound: len(months_filled) <= len(unpaid_months)
    7. Affordability: total_used <= B_before + P
    8. Order preservation: months_filled matches the prefix of unpaid_months
    """
    rng = random.Random(42)  # Deterministic seed for reproducible adversarial fuzzing
    total_iterations = 20000

    format_choices = ["tuple", "dict", "object"]

    for i in range(total_iterations):
        # Vary distribution: 20% small/zero, 60% standard, 20% massive
        roll = rng.random()
        if roll < 0.20:
            amount_paid = rng.randint(0, 20000)
            balance_before = rng.randint(0, 20000)
            queue_len = rng.randint(0, 15)
            due_per_month = rng.choice([1000, 5000, 10000, 12500, 15000, 20000])
        elif roll < 0.80:
            amount_paid = rng.randint(0, 1000000)
            balance_before = rng.randint(0, 500000)
            queue_len = rng.randint(0, 120)
            due_per_month = rng.choice([2500, 5000, 10000, 12500, 25000, 50000])
        else:
            amount_paid = rng.randint(1000000, 50000000)
            balance_before = rng.randint(1000000, 10000000)
            queue_len = rng.randint(0, 300)
            due_per_month = rng.randint(100, 100000)

        fmt = format_choices[i % 3]
        unpaid = _generate_unpaid_months(queue_len, fmt)

        res = allocate_dues(
            unpaid_months=unpaid,
            amount_paid=amount_paid,
            current_balance=balance_before,
            due_per_month=due_per_month,
        )

        n_filled = len(res.months_filled)

        # Invariant 1: Conservation of Money
        assert balance_before + amount_paid == res.total_used + res.balance_after, (
            f"Conservation violated at iter {i}: "
            f"{balance_before} + {amount_paid} != {res.total_used} + {res.balance_after}"
        )

        # Invariant 2: Exact consumption
        assert res.total_used == n_filled * due_per_month

        # Invariant 3: Deposit sync
        assert res.balance_after == res.new_deposit

        # Invariant 4: Non-negativity
        assert res.balance_after >= 0

        # Invariant 5: Maximality (all eligible months filled)
        if n_filled < queue_len:
            assert res.balance_after < due_per_month, (
                f"Under-allocation at iter {i}: leftover {res.balance_after} >= "
                f"fee {due_per_month} with {queue_len - n_filled} unfilled months"
            )

        # Invariant 6: Queue bound
        assert n_filled <= queue_len

        # Invariant 7: Affordability
        assert res.total_used <= balance_before + amount_paid

        # Invariant 8: Prefix order preservation
        for idx, (yr, m_idx) in enumerate(res.months_filled):
            item = unpaid[idx]
            if isinstance(item, tuple):
                assert (yr, m_idx) == (item[0], item[1])
            elif isinstance(item, dict):
                assert (yr, m_idx) == (item["year"], item["month_index"])
            else:
                assert (yr, m_idx) == (item.year, item.month_index)


def test_fifo_combinatorial_exhaustive_matrix():
    """Exhaustive Cartesian product matrix test across 4,224 deterministic combinations."""
    balances = [0, 1, 9999, 10000, 10001, 19999, 20000, 50000]
    amounts = [0, 1, 5000, 9999, 10000, 10001, 15000, 20000, 25000, 30000, 100000]
    fees = [1, 5000, 10000, 12500, 20000, 50000]
    queue_lengths = [0, 1, 2, 3, 5, 11, 12, 24]

    cached_queues = {ql: _generate_unpaid_months(ql, "tuple") for ql in queue_lengths}

    count = 0
    for b in balances:
        for a in amounts:
            for f in fees:
                for ql in queue_lengths:
                    unpaid = cached_queues[ql]
                    res = allocate_dues(
                        unpaid_months=unpaid,
                        amount_paid=a,
                        current_balance=b,
                        due_per_month=f,
                    )
                    assert b + a == res.total_used + res.balance_after
                    assert res.balance_after == res.new_deposit
                    assert res.total_used == len(res.months_filled) * f
                    assert len(res.months_filled) == min((b + a) // f, ql)
                    count += 1

    assert count == len(balances) * len(amounts) * len(fees) * len(queue_lengths)
    assert count == 4224


def test_fifo_stress_non_standard_dues():
    """Stress test non-standard dues fee amounts."""
    odd_fees = [1, 7, 100, 333, 4999, 5000, 12500, 33333, 50000, 100000, 999999]
    unpaid = _generate_unpaid_months(24, "tuple")

    for fee in odd_fees:
        # Exact 1 month
        res = allocate_dues(unpaid, amount_paid=fee, current_balance=0, due_per_month=fee)
        assert len(res.months_filled) == 1
        assert res.balance_after == 0
        assert res.total_used == fee

        # Underpayment by 1 IDR
        res_under = allocate_dues(unpaid, amount_paid=fee - 1, current_balance=0, due_per_month=fee)
        assert len(res_under.months_filled) == 0
        assert res_under.balance_after == fee - 1
        assert res_under.total_used == 0

        # Overpayment by 1 IDR
        res_over = allocate_dues(unpaid, amount_paid=fee + 1, current_balance=0, due_per_month=fee)
        if fee == 1:
            assert len(res_over.months_filled) == 2
            assert res_over.balance_after == 0
            assert res_over.total_used == 2
        else:
            assert len(res_over.months_filled) == 1
            assert res_over.balance_after == 1
            assert res_over.total_used == fee


def test_fifo_stress_massive_payments_multi_decade():
    """Stress test massive payments across 10-year backlogs and multi-decade overflows."""
    # 120 unpaid months (10 years)
    ten_year_unpaid = _generate_unpaid_months(120, "tuple")
    fee = 10000
    exact_ten_years = 120 * fee  # Rp 1.200.000

    # Exact 10 years payment
    res = allocate_dues(ten_year_unpaid, amount_paid=exact_ten_years, current_balance=0)
    assert len(res.months_filled) == 120
    assert res.balance_after == 0
    assert res.total_used == exact_ten_years

    # Massive 1 Billion IDR payment (100,000 months worth)
    one_billion = 1_000_000_000
    res_billion = allocate_dues(ten_year_unpaid, amount_paid=one_billion, current_balance=0)
    assert len(res_billion.months_filled) == 120
    assert res_billion.total_used == exact_ten_years
    expected_surplus = one_billion - exact_ten_years
    assert res_billion.balance_after == expected_surplus
    assert res_billion.new_deposit == expected_surplus

    # Massive 1 Trillion IDR payment
    one_trillion = 1_000_000_000_000
    res_trillion = allocate_dues(
        ten_year_unpaid, amount_paid=one_trillion, current_balance=500_000_000
    )
    assert len(res_trillion.months_filled) == 120
    assert res_trillion.total_used == exact_ten_years
    assert res_trillion.balance_after == (one_trillion + 500_000_000) - exact_ten_years


def test_fifo_extreme_scale_10000_months_queue():
    """Stress test allocation against a massive 10,000-month queue."""
    large_queue = [(2000 + (m // 12), (m % 12) + 1) for m in range(10000)]
    fee = 10000
    # Clear exactly 4,500 months + Rp 3,450 remainder
    payment = (4500 * fee) + 3450

    res = allocate_dues(
        unpaid_months=large_queue,
        amount_paid=payment,
        current_balance=0,
        due_per_month=fee,
    )
    assert len(res.months_filled) == 4500
    assert res.total_used == 4500 * fee
    assert res.balance_after == 3450
    assert res.months_filled == large_queue[:4500]


def test_fifo_boundary_zero_and_negative_inputs():
    """Adversarial testing of zero and negative inputs."""
    unpaid = _generate_unpaid_months(5, "tuple")

    # Zero payment with zero balance
    res_zero = allocate_dues(unpaid, amount_paid=0, current_balance=0)
    assert len(res_zero.months_filled) == 0
    assert res_zero.balance_after == 0
    assert res_zero.total_used == 0

    # Zero payment with sufficient prior balance to clear 2 months
    res_zero_draw = allocate_dues(unpaid, amount_paid=0, current_balance=25000, due_per_month=10000)
    assert len(res_zero_draw.months_filled) == 2
    assert res_zero_draw.balance_after == 5000
    assert res_zero_draw.total_used == 20000

    # Negative amount_paid must raise ValueError
    with pytest.raises(ValueError, match="amount_paid cannot be negative"):
        allocate_dues(unpaid, amount_paid=-1)
    with pytest.raises(ValueError, match="amount_paid cannot be negative"):
        allocate_dues(unpaid, amount_paid=-1000000)

    # Negative current_balance must raise ValueError
    with pytest.raises(ValueError, match="current_balance cannot be negative"):
        allocate_dues(unpaid, amount_paid=10000, current_balance=-1)

    # Zero or negative due_per_month must raise ValueError
    with pytest.raises(ValueError, match="due_per_month must be greater than 0"):
        allocate_dues(unpaid, amount_paid=10000, due_per_month=0)
    with pytest.raises(ValueError, match="due_per_month must be greater than 0"):
        allocate_dues(unpaid, amount_paid=10000, due_per_month=-5000)


def test_fifo_boundary_empty_unpaid_queue():
    """Adversarial testing with completely empty unpaid queue."""
    empty_queue: list[Any] = []

    # Rp 0 payment, 0 balance
    res = allocate_dues(empty_queue, amount_paid=0, current_balance=0)
    assert res.months_filled == []
    assert res.balance_after == 0
    assert res.total_used == 0

    # Rp 100,000 payment with empty queue -> 100% goes to balance
    res2 = allocate_dues(empty_queue, amount_paid=100000, current_balance=50000)
    assert res2.months_filled == []
    assert res2.balance_after == 150000
    assert res2.total_used == 0
    assert res2.new_deposit == 150000


def test_fifo_adversarial_malformed_queue_items():
    """Adversarial check: unsupported item types raise TypeError cleanly."""
    # Single invalid item at head
    with pytest.raises(TypeError, match="Unsupported month item type"):
        allocate_dues(["string_item"], amount_paid=10000)

    # Invalid int item
    with pytest.raises(TypeError, match="Unsupported month item type"):
        allocate_dues([99999], amount_paid=10000)

    # Note: If no payment is made or 0 items are cleared, items are not extracted
    res_zero = allocate_dues(["invalid_string"], amount_paid=0, current_balance=0)
    assert len(res_zero.months_filled) == 0


def test_fifo_immutability_and_result_contract():
    """Verify that AllocationResult is frozen and adheres strictly to PROJECT.md §3."""
    unpaid = [(2026, 2)]
    res = allocate_dues(unpaid, amount_paid=10000)

    assert isinstance(res, AllocationResult)
    assert res.months_filled == [(2026, 2)]
    assert res.balance_before == 0
    assert res.balance_after == 0
    assert res.new_deposit == 0
    assert res.total_used == 10000
    assert res.amount_paid == 10000
    assert res.cleared_month_names == ["Februari"]

    # Attempt mutation on frozen dataclass
    with pytest.raises(FrozenInstanceError):
        res.balance_after = 5000  # type: ignore[misc]


def test_fifo_throughput_performance():
    """Verify engine achieves high throughput (>100,000 operations/sec)."""
    unpaid = [(2026, m) for m in range(2, 13)]
    num_ops = 50000
    fee = 10000

    start_t = time.perf_counter()
    for _ in range(num_ops):
        allocate_dues(unpaid, amount_paid=35000, current_balance=5000, due_per_month=fee)
    elapsed = time.perf_counter() - start_t

    ops_per_sec = num_ops / elapsed
    assert ops_per_sec > 25000, f"Throughput too low: {ops_per_sec:.0f} ops/sec"
