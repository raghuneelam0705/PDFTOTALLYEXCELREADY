from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

from app.parsers.base import BankPdfParser
from app.parsers.types import NormalizedRow, RawRowLike

DATE_PATTERN = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
NUMBER_PATTERN = re.compile(r"^-?[0-9][0-9,]*(?:\.\d+)?(?:\s*(?:CR|DR))?$", re.IGNORECASE)
TXN_LINE_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<narration>.+?)\s+"
    r"(?P<ref>[A-Z0-9./-]+)\s+"
    r"(?P<valuedt>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<amount>[0-9,]+\.\d{2})\s+"
    r"(?P<balance>[0-9,]+\.\d{2})$",
    re.IGNORECASE,
)
OPENING_BAL_RE = re.compile(
    r"^(?P<opening>[0-9,]+\.\d{2})\s+\d+\s+\d+\s+[0-9,]+\.\d{2}\s+[0-9,]+\.\d{2}\s+[0-9,]+\.\d{2}$"
)


@dataclass
class _ColumnMap:
    date_idx: int = 0
    narration_idx: int = 1
    ref_idx: int = 2
    withdrawal_idx: int = 4
    deposit_idx: int = 5
    balance_idx: int = 6


@dataclass
class _PendingTxn:
    date: str
    particulars: str
    debit: Optional[Decimal]
    credit: Optional[Decimal]
    balance: Decimal


@dataclass
class _TextTxn:
    date: str
    particulars: str
    amount: Decimal
    balance: Decimal


class HdfcPdfParser(BankPdfParser):
    bank_code = "hdfc"
    bank_name = "HDFC Bank"

    def can_parse(self, raw_rows: Sequence[RawRowLike]) -> bool:
        joined = " ".join(_compact_text(row).lower() for row in raw_rows[:80])
        squashed = re.sub(r"[^a-z0-9]+", "", joined)
        has_bank_name = "hdfcbank" in squashed
        has_hdfc_columns = "withdrawalamt" in squashed and "depositamt" in squashed
        return has_bank_name or has_hdfc_columns

    def parse(self, raw_rows: Sequence[RawRowLike]) -> list[NormalizedRow]:
        if not raw_rows:
            return []

        columns = _ColumnMap()
        parsed: list[NormalizedRow] = []
        pending: Optional[_PendingTxn] = None

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

        if parsed:
            return parsed

        text_parsed = _parse_from_text_rows(raw_rows)
        if text_parsed:
            return text_parsed

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


def _derive_column_map_from_header(cells: list[str]) -> Optional[_ColumnMap]:
    lowered = [cell.lower() for cell in cells]
    if not any("closing balance" in cell or "balance" == cell.strip() for cell in lowered):
        return None
    if not any("withdrawal" in cell or "debit" in cell for cell in lowered):
        return None
    if not any("deposit" in cell or "credit" in cell for cell in lowered):
        return None

    mapping = _ColumnMap()
    for idx, token in enumerate(lowered):
        if "date" in token and "value" not in token:
            mapping.date_idx = idx
        elif "narration" in token or "particular" in token or "description" in token:
            mapping.narration_idx = idx
        elif "ref" in token or "chq" in token or "cheque" in token:
            mapping.ref_idx = idx
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
    narration = _cell(cells, columns.narration_idx)
    reference = _cell(cells, columns.ref_idx)
    particulars = " ".join([value for value in [narration, reference] if value]).strip()

    debit = _parse_amount_optional(_cell(cells, columns.withdrawal_idx))
    credit = _parse_amount_optional(_cell(cells, columns.deposit_idx))
    balance = _parse_amount_required(_cell(cells, columns.balance_idx))

    if (debit is None) == (credit is None):
        raise ValueError(
            f"HDFC parse error on {date}: exactly one of withdrawal/deposit should be set."
        )

    return _PendingTxn(date=date, particulars=particulars, debit=debit, credit=credit, balance=balance)


def _extract_continuation_text(cells: list[str], columns: _ColumnMap) -> str:
    if _is_transaction_start(cells, columns):
        return ""

    narrative = _cell(cells, columns.narration_idx)
    ref = _cell(cells, columns.ref_idx)
    combined = " ".join([value for value in [narrative, ref] if value]).strip()
    if combined:
        return combined

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


def _parse_amount_optional(raw_amount: str) -> Optional[Decimal]:
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


def _parse_from_text_rows(raw_rows: Sequence[RawRowLike]) -> list[NormalizedRow]:
    lines: list[str] = []
    for row in raw_rows:
        text = str(row.get("text", "")).strip()  # type: ignore[attr-defined]
        if not text:
            continue
        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned:
                lines.append(cleaned)

    if not lines:
        return []

    txns: list[_TextTxn] = []
    current: Optional[_TextTxn] = None
    opening_balance: Optional[Decimal] = None

    for line in lines:
        opening_match = OPENING_BAL_RE.match(line)
        if opening_match:
            opening_balance = _parse_amount_required(opening_match.group("opening"))
            continue

        txn_match = TXN_LINE_RE.match(line)
        if txn_match:
            if current:
                txns.append(current)

            date = _normalize_date(txn_match.group("date"))
            narration = txn_match.group("narration").strip()
            ref = txn_match.group("ref").strip()
            amount = _parse_amount_required(txn_match.group("amount"))
            balance = _parse_amount_required(txn_match.group("balance"))

            particulars = " ".join([part for part in [narration, ref] if part]).strip()
            current = _TextTxn(date=date, particulars=particulars, amount=amount, balance=balance)
            continue

        if current and _is_narration_continuation(line):
            current.particulars = f"{current.particulars} {line}".strip()

    if current:
        txns.append(current)

    if not txns:
        return []

    return _finalize_text_transactions(txns, opening_balance)


def _is_narration_continuation(line: str) -> bool:
    lowered = line.lower()
    if "|" in line:
        return False
    if line.isdigit():
        return False
    if len(line) <= 3:
        return False
    if lowered.startswith("hdfcbanklimited") or lowered.startswith("statementsummary"):
        return False
    if lowered.startswith("from :") or lowered.startswith("page"):
        return False
    if lowered.startswith("generatedon:") or lowered.startswith("thisisacomputergenerated"):
        return False
    if lowered.startswith("contentsofthisstatement") or lowered.startswith("stateaccountbranch"):
        return False
    if lowered.startswith("hdfcbankgstinnumber") or lowered.startswith("registeredofficeaddress"):
        return False
    if lowered.startswith("*closingbalanceincludes"):
        return False
    if lowered.startswith("openingbalance") or lowered.startswith("generatedby:"):
        return False
    if "notrequiresignature" in lowered or lowered == "thisstatement.":
        return False
    if "statementof account" in lowered:
        return False
    if "accountbranch" in lowered or "phoneno." in lowered or "custid" in lowered:
        return False
    if DATE_PATTERN.match(line):
        return False
    return True


def _finalize_text_transactions(
    txns: list[_TextTxn], opening_balance: Optional[Decimal]
) -> list[NormalizedRow]:
    normalized: list[NormalizedRow] = []
    prev_balance = opening_balance

    for idx, txn in enumerate(txns):
        inferred_delta: Optional[Decimal] = None
        if prev_balance is not None:
            inferred_delta = txn.balance - prev_balance

        debit: Optional[Decimal] = None
        credit: Optional[Decimal] = None

        if inferred_delta is not None and inferred_delta != Decimal("0"):
            abs_delta = abs(inferred_delta).quantize(Decimal("0.01"))
            stated_amount = txn.amount.quantize(Decimal("0.01"))
            if abs_delta != stated_amount:
                raise ValueError(
                    f"HDFC parse error on row {idx + 1} ({txn.date}): "
                    f"amount {stated_amount} does not match balance delta {abs_delta}."
                )
            if inferred_delta > 0:
                credit = stated_amount
            else:
                debit = stated_amount
        elif inferred_delta == Decimal("0"):
            raise ValueError(
                f"HDFC parse error on row {idx + 1} ({txn.date}): balance did not change."
            )
        else:
            # Fallback for first row when opening balance is unavailable.
            if "cr-" in txn.particulars.lower() or txn.particulars.lower().startswith("rtgscr"):
                credit = txn.amount
            elif "dr-" in txn.particulars.lower():
                debit = txn.amount
            else:
                raise ValueError(
                    f"HDFC parse error on row {idx + 1} ({txn.date}): "
                    "unable to infer debit/credit without opening balance."
                )

        normalized.append(
            NormalizedRow(
                date=txn.date,
                particulars=txn.particulars.strip(),
                debit=debit,
                credit=credit,
                balance=txn.balance,
            )
        )
        prev_balance = txn.balance

    return normalized
