from __future__ import annotations

from typing import Sequence

from app.parsers.base import BankPdfParser
from app.parsers.types import RawRowLike


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, type[BankPdfParser]] = {}

    def register(self, parser_cls: type[BankPdfParser]) -> None:
        code = parser_cls.bank_code.strip().lower()
        if not code:
            raise ValueError("Parser bank_code cannot be empty.")
        self._parsers[code] = parser_cls

    def create(self, bank_code: str) -> BankPdfParser:
        parser_cls = self._parsers.get(bank_code.strip().lower())
        if parser_cls is None:
            raise KeyError(f"No parser registered for bank code: {bank_code}")
        return parser_cls()

    def list_bank_codes(self) -> list[str]:
        return sorted(self._parsers.keys())

    def detect(self, raw_rows: Sequence[RawRowLike]) -> BankPdfParser | None:
        for parser_cls in self._parsers.values():
            parser = parser_cls()
            if parser.can_parse(raw_rows):
                return parser
        return None

