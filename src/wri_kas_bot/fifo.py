"""Deterministic FIFO dues allocation queue engine for wri-kas-bot.

Chronologically allocates available treasury funds (starting balance + new payment)
across unpaid monthly dues. Supports partial deposits, exact payments, odd surplus,
and advance payments.
"""

from dataclasses import dataclass, field
from typing import Any

DEFAULT_DUE_PER_MONTH: int = 10000

MONTH_INDEX_TO_NAME: dict[int, str] = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}


@dataclass(frozen=True)
class AllocationResult:
    """Result of deterministic FIFO dues allocation."""

    months_filled: list[tuple[int, int]]  # [(year, month_index), ...]
    balance_before: int
    balance_after: int
    new_deposit: int
    total_used: int = 0
    amount_paid: int = 0
    cleared_month_names: list[str] = field(default_factory=list)


def _extract_year_month(item: Any) -> tuple[int, int, str]:
    """Extract (year, month_index, month_name) from tuple, dict, or object."""
    if isinstance(item, tuple):
        year, month_idx = int(item[0]), int(item[1])
        name = MONTH_INDEX_TO_NAME.get(month_idx, f"Bulan-{month_idx}")
        return year, month_idx, name
    elif isinstance(item, dict):
        year = int(item["year"])
        month_idx = int(item["month_index"])
        name = str(item.get("month", MONTH_INDEX_TO_NAME.get(month_idx, f"Bulan-{month_idx}")))
        return year, month_idx, name
    elif hasattr(item, "year") and hasattr(item, "month_index"):
        year = int(item.year)
        month_idx = int(item.month_index)
        name = str(getattr(item, "month", MONTH_INDEX_TO_NAME.get(month_idx, f"Bulan-{month_idx}")))
        return year, month_idx, name
    raise TypeError(f"Unsupported month item type: {type(item)}")


def allocate_dues(
    unpaid_months: list[Any],
    amount_paid: int | float,
    current_balance: int | float = 0,
    due_per_month: int | float = DEFAULT_DUE_PER_MONTH,
) -> AllocationResult:
    """Deterministically allocate payment across chronological unpaid dues.

    Mathematical Model:
        T = B_before + P
        N_fill = min(floor(T / due_per_month), len(unpaid_months))
        C_used = N_fill * due_per_month
        B_after = T - C_used

    Args:
        unpaid_months: Chronologically sorted unpaid months (tuples, dicts, or MonthlyDue objects).
        amount_paid: Nominal payment in IDR (must be >= 0).
        current_balance: Starting member deposit balance in IDR (must be >= 0).
        due_per_month: Required fee per single month (must be > 0, defaults to 10,000 IDR).

    Returns:
        AllocationResult with filled month tuples, balance updates, and cleared names.

    Raises:
        ValueError: If amount_paid < 0, current_balance < 0, or due_per_month <= 0.
    """
    if amount_paid < 0:
        raise ValueError("amount_paid cannot be negative")
    if current_balance < 0:
        raise ValueError("current_balance cannot be negative")
    if due_per_month <= 0:
        raise ValueError("due_per_month must be greater than 0")

    amount_paid_int = int(amount_paid)
    current_balance_int = int(current_balance)
    due_per_month_int = int(due_per_month)

    total_pool = current_balance_int + amount_paid_int
    num_eligible = int(min(total_pool // due_per_month_int, len(unpaid_months)))

    cleared_items = unpaid_months[:num_eligible]
    total_used = num_eligible * due_per_month_int
    balance_after = total_pool - total_used

    months_filled: list[tuple[int, int]] = []
    month_names: list[str] = []

    for item in cleared_items:
        y, m_idx, m_name = _extract_year_month(item)
        months_filled.append((y, m_idx))
        month_names.append(m_name)

    return AllocationResult(
        months_filled=months_filled,
        balance_before=current_balance_int,
        balance_after=balance_after,
        new_deposit=balance_after,
        total_used=total_used,
        amount_paid=amount_paid_int,
        cleared_month_names=month_names,
    )
