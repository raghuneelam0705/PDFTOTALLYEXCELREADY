from io import BytesIO

import pandas as pd

from app.parsers.hdfc import HdfcPdfParser
from app.parsers.base import BankPdfParser
from app.parsers.registry import ParserRegistry
from app.parsers.sbi import SbiPdfParser
from app.services.pdf_extractor import extract_raw_rows_from_pdf
from app.services.validation import validate_running_balance

TALLY_COLUMNS = ["Date", "Particulars", "Debit", "Credit", "Balance"]
PARSER_REGISTRY = ParserRegistry()
PARSER_REGISTRY.register(HdfcPdfParser)
PARSER_REGISTRY.register(SbiPdfParser)


def parse_pdf_records(pdf_bytes: bytes) -> tuple[list[dict[str, str]], BankPdfParser]:
    raw_rows = extract_raw_rows_from_pdf(pdf_bytes)
    parser = PARSER_REGISTRY.detect(raw_rows)
    if parser is None:
        supported = ", ".join(PARSER_REGISTRY.list_bank_codes())
        raise ValueError(f"Unsupported bank statement format. Supported banks: {supported}")

    normalized_rows = parser.parse(raw_rows)

    mismatches = validate_running_balance(normalized_rows)
    if mismatches:
        details = "; ".join(mismatch.to_message() for mismatch in mismatches)
        raise ValueError(f"Running balance validation failed: {details}")

    records = [row.to_record() for row in normalized_rows]
    if not records:
        return []

    cleaned: list[dict[str, str]] = []
    for record in records:
        cleaned.append(
            {
                "Date": str(record.get("Date", "")).strip(),
                "Particulars": str(record.get("Particulars", "")).strip(),
                "Debit": str(record.get("Debit", "")).replace(",", "").strip(),
                "Credit": str(record.get("Credit", "")).replace(",", "").strip(),
                "Balance": str(record.get("Balance", "")).replace(",", "").strip(),
            }
        )
    return cleaned, parser


def pdf_to_tally_records(pdf_bytes: bytes) -> list[dict[str, str]]:
    records, _ = parse_pdf_records(pdf_bytes)
    return records


def records_to_excel_bytes(records: list[dict[str, str]]) -> BytesIO:
    df = pd.DataFrame(records, columns=TALLY_COLUMNS).fillna("")

    # Keep all fields as plain strings for predictable import behavior.
    for column in TALLY_COLUMNS:
        df[column] = df[column].map(lambda v: str(v).replace(",", "") if v is not None else "")

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")

    output.seek(0)
    return output


def pdf_to_excel_bytes(pdf_bytes: bytes) -> BytesIO:
    records = pdf_to_tally_records(pdf_bytes)
    return records_to_excel_bytes(records)
