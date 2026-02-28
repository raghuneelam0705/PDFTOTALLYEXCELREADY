from io import BytesIO

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.services.converter import (
    TALLY_COLUMNS,
    parse_pdf_records,
    pdf_to_excel_bytes,
    pdf_to_tally_records,
    records_to_excel_bytes,
)
from app.services.session_store import SESSION_STORE

app = FastAPI(title="PDF to Excel (Localhost)")
templates = Jinja2Templates(directory="app/templates")


def _ensure_pdf(file: UploadFile) -> None:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")


def _build_output_name(file_name: str) -> str:
    return (file_name or "statement.pdf").rsplit(".", 1)[0] + ".xlsx"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze")
async def analyze_pdf(file: UploadFile = File(...)) -> dict[str, object]:
    _ensure_pdf(file)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        rows, parser = await run_in_threadpool(parse_pdf_records, pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session = SESSION_STORE.create(
        file_name=file.filename or "statement.pdf",
        parser_code=parser.bank_code,
        rows=rows,
    )
    return {
        "session_id": session.session_id,
        "columns": TALLY_COLUMNS,
        "total_rows": len(rows),
        "parser_code": parser.bank_code,
        "file_name": session.file_name,
    }


@app.get("/preview/{session_id}")
async def preview_page(
    session_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    session = SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired. Re-upload the PDF.")

    total = len(session.rows)
    end = min(offset + limit, total)
    rows = session.rows[offset:end]
    return {
        "columns": TALLY_COLUMNS,
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "count": len(rows),
        "total_rows": total,
        "has_more": end < total,
    }


@app.get("/export/{session_id}")
async def export_session(session_id: str) -> StreamingResponse:
    session = SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired. Re-upload the PDF.")

    excel_data: BytesIO = await run_in_threadpool(records_to_excel_bytes, session.rows)
    headers = {"Content-Disposition": f'attachment; filename="{_build_output_name(session.file_name)}"'}
    return StreamingResponse(
        excel_data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.post("/convert")
async def convert_pdf(file: UploadFile = File(...)) -> StreamingResponse:
    _ensure_pdf(file)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        excel_data: BytesIO = await run_in_threadpool(pdf_to_excel_bytes, pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    headers = {"Content-Disposition": f'attachment; filename="{_build_output_name(file.filename or "")}"'}
    return StreamingResponse(
        excel_data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.post("/preview")
async def preview_pdf(file: UploadFile = File(...)) -> dict[str, object]:
    _ensure_pdf(file)
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        rows = await run_in_threadpool(pdf_to_tally_records, pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"columns": TALLY_COLUMNS, "rows": rows}

