from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.parsers.types import NormalizedRow

PAISE = Decimal("0.01")


@dataclass(frozen=True)
class BalanceMismatch:
    row_number: int
    date: str
    expected_balance: Decimal
    actual_balance: Decimal

    def to_message(self) -> str:
        return (
            f"Row {self.row_number} ({self.date}): "
            f"expected balance {self.expected_balance}, got {self.actual_balance}"
        )


def validate_running_balance(rows: list[NormalizedRow]) -> list[BalanceMismatch]:
    mismatches: list[BalanceMismatch] = []
    if len(rows) < 2:
        return mismatches

    for idx in range(1, len(rows)):
        prev = rows[idx - 1]
        curr = rows[idx]

        debit = curr.debit or Decimal("0")
        credit = curr.credit or Decimal("0")
        expected = (prev.balance + credit - debit).quantize(PAISE, rounding=ROUND_HALF_UP)
        actual = curr.balance.quantize(PAISE, rounding=ROUND_HALF_UP)

        if expected != actual:
            mismatches.append(
                BalanceMismatch(
                    row_number=idx + 1,
                    date=curr.date,
                    expected_balance=expected,
                    actual_balance=actual,
                )
            )

    return mismatches

