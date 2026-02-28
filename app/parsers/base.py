from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from app.parsers.types import NormalizedRow, RawRowLike


class BankPdfParser(ABC):
    bank_code: str
    bank_name: str

    def can_parse(self, raw_rows: Sequence[RawRowLike]) -> bool:
        return False

    @abstractmethod
    def parse(self, raw_rows: Sequence[RawRowLike]) -> list[NormalizedRow]:
        """Convert extracted raw rows into normalized bank statement rows."""

