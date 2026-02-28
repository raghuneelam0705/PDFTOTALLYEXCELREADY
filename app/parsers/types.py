from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, TypedDict, Union


class RawExtractedRow(TypedDict, total=False):
    page: int
    line_no: int
    text: str
    cells: list[str]
    meta: dict[str, Any]


@dataclass(frozen=True)
class NormalizedRow:
    date: str
    particulars: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal

    def __post_init__(self) -> None:
        has_debit = self.debit is not None
        has_credit = self.credit is not None
        if has_debit == has_credit:
            raise ValueError("Exactly one of debit or credit must be set.")

    def to_record(self) -> dict[str, str]:
        return {
            "Date": self.date,
            "Particulars": self.particulars,
            "Debit": _amount_to_string(self.debit),
            "Credit": _amount_to_string(self.credit),
            "Balance": _amount_to_string(self.balance),
        }


def _amount_to_string(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


RawRowLike = Union[Mapping[str, Any], RawExtractedRow]
