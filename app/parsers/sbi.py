from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Sequence

from app.parsers.base import BankPdfParser
from app.parsers.types import NormalizedRow, RawRowLike

DATE_PATTERN = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
NUMBER_PATTERN = re.compile(r"^-?[0-9][0-9,]*(?:\.\d+)?(?:\s*(?:CR|DR))?$", re.IGNORECASE)


@dataclass
class _ColumnMap:
    date_idx: int = 0
    particulars_idx: int = 2
    withdrawal_idx: int = 3
    deposit_idx: int = 4
    balance_idx: int = 5


@dataclass
class _PendingTxn:
    date: str
    particulars: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal


class SbiPdfParser(BankPdfParser):
    bank_code = "sbi"
    bank_name = "State Bank of India"

    def can_parse(self, raw_rows: Sequence[RawRowLike]) -> bool:
        joined = " ".join(_compact_text(row).lower() for row in raw_rows[:60])
        squashed = re.sub(r"[^a-z0-9]+", "", joined)
        if "statebankofindia" in squashed:
            return True
        if "hdfcbank" in squashed:
            return False

        has_columns = (
            ("withdrawal" in joined or "debit" in joined)
            and ("deposit" in joined or "credit" in joined)
            and "balance" in joined
        )
        looks_like_hdfc = "valuedt" in squashed or "withdrawalamt" in squashed or "chqrefno" in squashed
        return has_columns and not looks_like_hdfc

    def parse(self, raw_rows: Sequence[RawRowLike]) -> list[NormalizedRow]:
        if not raw_rows:
            return []

        columns = _ColumnMap()
        parsed: list[NormalizedRow] = []
        pending: _PendingTxn | None = None

        for row in raw_rows:
            cells = _cells(row)
            if not cells:
                continue

            header_map = _derive_column_map_from_header(cells)
            if header_map:
                columns = header_map
                continue

            if _is_transaction_start(cells, columns):
                if pending:
                    parsed.append(_to_normalized_row(pending))

                pending = _create_pending_transaction(cells, columns)
                continue

            if pending:
                continuation = _extract_continuation_text(cells, columns)
                if continuation:
                    pending.particulars = f"{pending.particulars} {continuation}".strip()

        if pending:
            parsed.append(_to_normalized_row(pending))

        return parsed


def _cells(row: RawRowLike) -> list[str]:
    raw_cells = row.get("cells")  # type: ignore[attr-defined]
    if isinstance(raw_cells, list):
        return [str(cell).strip() for cell in raw_cells]
    text = str(row.get("text", "")).strip()  # type: ignore[attr-defined]
    return [segment.strip() for segment in text.split("|")] if text else []


def _compact_text(row: RawRowLike) -> str:
    cells = _cells(row)
    if cells:
        return " ".join(cells)
    return str(row.get("text", ""))  # type: ignore[attr-defined]


def _derive_column_map_from_header(cells: list[str]) -> _ColumnMap | None:
    lowered = [cell.lower() for cell in cells]
    if not any("balance" in cell for cell in lowered):
        return None
    if not any("withdraw" in cell or "debit" in cell for cell in lowered):
        return None
    if not any("deposit" in cell or "credit" in cell for cell in lowered):
        return None

    mapping = _ColumnMap()
    for idx, token in enumerate(lowered):
        if "date" in token and "value" not in token:
            mapping.date_idx = idx
        elif "description" in token or "narration" in token or "particular" in token:
            mapping.particulars_idx = idx
        elif "withdraw" in token or "debit" in token:
            mapping.withdrawal_idx = idx
        elif "deposit" in token or "credit" in token:
            mapping.deposit_idx = idx
        elif "balance" in token:
            mapping.balance_idx = idx
    return mapping


def _is_transaction_start(cells: list[str], columns: _ColumnMap) -> bool:
    date_cell = _cell(cells, columns.date_idx)
    if not DATE_PATTERN.match(date_cell):
        return False
    balance_cell = _cell(cells, columns.balance_idx)
    return bool(balance_cell and NUMBER_PATTERN.match(balance_cell))


def _create_pending_transaction(cells: list[str], columns: _ColumnMap) -> _PendingTxn:
    date = _normalize_date(_cell(cells, columns.date_idx))
    particulars = _cell(cells, columns.particulars_idx)
    debit = _parse_amount_optional(_cell(cells, columns.withdrawal_idx))
    credit = _parse_amount_optional(_cell(cells, columns.deposit_idx))
    balance = _parse_amount_required(_cell(cells, columns.balance_idx))

    if (debit is None) == (credit is None):
        raise ValueError(
            f"SBI parse error on {date}: exactly one of withdrawal/deposit should be set."
        )

    return _PendingTxn(date=date, particulars=particulars, debit=debit, credit=credit, balance=balance)


def _extract_continuation_text(cells: list[str], columns: _ColumnMap) -> str:
    if _is_transaction_start(cells, columns):
        return ""

    description = _cell(cells, columns.particulars_idx)
    if description:
        return description

    non_numeric = [
        token
        for token in cells
        if token and not DATE_PATTERN.match(token) and not NUMBER_PATTERN.match(token)
    ]
    return " ".join(non_numeric).strip()


def _to_normalized_row(txn: _PendingTxn) -> NormalizedRow:
    return NormalizedRow(
        date=txn.date,
        particulars=txn.particulars.strip(),
        debit=txn.debit,
        credit=txn.credit,
        balance=txn.balance,
    )


def _cell(cells: list[str], idx: int) -> str:
    if idx < 0:
        idx = len(cells) + idx
    if 0 <= idx < len(cells):
        return cells[idx].strip()
    return ""


def _normalize_date(raw_date: str) -> str:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw_date, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {raw_date}")


def _parse_amount_optional(raw_amount: str) -> Decimal | None:
    cleaned = raw_amount.strip()
    if not cleaned or cleaned == "-":
        return None
    return _parse_amount_required(cleaned)


def _parse_amount_required(raw_amount: str) -> Decimal:
    cleaned = raw_amount.strip().replace(",", "")
    suffix = ""
    upper = cleaned.upper()
    if upper.endswith("CR") or upper.endswith("DR"):
        suffix = upper[-2:]
        cleaned = cleaned[:-2].strip()

    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount value: {raw_amount}") from exc

    if suffix == "DR":
        return -abs(value)
    return abs(value)
