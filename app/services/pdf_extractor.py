from __future__ import annotations

from io import BytesIO
import re

import pdfplumber

from app.parsers.types import RawExtractedRow


def extract_raw_rows_from_pdf(pdf_bytes: bytes) -> list[RawExtractedRow]:
    rows: list[RawExtractedRow] = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            page_rows = _extract_table_rows(page_index, page)
            if _should_fallback_to_text(page_rows):
                page_rows = []
            if page_rows:
                rows.extend(page_rows)
                continue

            rows.extend(_extract_text_rows(page_index, page))

    return rows


def _extract_table_rows(page_number: int, page: pdfplumber.page.Page) -> list[RawExtractedRow]:
    extracted: list[RawExtractedRow] = []
    tables = page.extract_tables(
        table_settings={
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "intersection_tolerance": 3,
        }
    )

    line_no = 0
    for table in tables or []:
        for raw_cells in table or []:
            cells = [((cell or "").strip()) for cell in raw_cells]
            if not any(cells):
                continue

            line_no += 1
            extracted.append(
                {
                    "page": page_number,
                    "line_no": line_no,
                    "cells": cells,
                    "text": " | ".join([c for c in cells if c]),
                }
            )

    return extracted


def _extract_text_rows(page_number: int, page: pdfplumber.page.Page) -> list[RawExtractedRow]:
    extracted: list[RawExtractedRow] = []
    lines = (page.extract_text() or "").splitlines()
    for idx, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            continue
        extracted.append({"page": page_number, "line_no": idx, "text": text, "cells": [text]})
    return extracted


def _should_fallback_to_text(rows: list[RawExtractedRow]) -> bool:
    if not rows:
        return False

    date_hits = 0
    dense_multiline_cells = 0
    for row in rows:
        for cell in row.get("cells", []):
            if "\n" in cell:
                dense_multiline_cells += 1
                date_hits += len(re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", cell))

    # Some HDFC PDFs collapse whole page transactions into dense multi-line cells.
    return dense_multiline_cells > 0 and date_hits >= 6
