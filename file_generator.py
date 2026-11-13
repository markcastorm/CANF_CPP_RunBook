"""
Writes pipeline outputs from extracted CPP investment-portfolio data.

Responsibilities
----------------
1. update_master_csv(new_rows)
       Upserts extracted rows into the master CSV (creates if absent).

2. generate_outputs(run_dir)
       Reads master CSV and writes to run_dir:
         CANF_CPP_DATA_<ts>.xlsx   — DATA workbook (all quarters × 23 codes)
         CANF_CPP_META_<ts>.xlsx   — META workbook (one row per series)
         CANF_CPP_<ts>.zip         — ZIP of both xlsx files
       Then syncs run_dir into output/latest/.

Public helpers
--------------
make_run_dir()   → Path  — creates timestamped folder under config.OUTPUT_DIR
"""

from __future__ import annotations

import csv
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import config

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

HEADER_FILL  = PatternFill('solid', fgColor='1F4E79')
HEADER_FONT  = Font(color='FFFFFF', bold=True, name='Calibri', size=10)
BODY_FONT    = Font(name='Calibri', size=10)
ALIGN_CENTER = Alignment(horizontal='center', vertical='center')
ALIGN_LEFT   = Alignment(horizontal='left',   vertical='center')
ALIGN_RIGHT  = Alignment(horizontal='right',  vertical='center')

META_COLUMNS = [
    'CODE', 'DESCRIPTION',
    'FREQUENCY', 'MULTIPLIER', 'AGGREGATION_TYPE', 'UNIT_TYPE',
    'DATA_TYPE', 'DATA_UNIT', 'SEASONALLY_ADJUSTED', 'ANNUALIZED',
    'STATE', 'PROVIDER_MEASURE_URL', 'PROVIDER', 'SOURCE',
    'SOURCE_DESCRIPTION', 'COUNTRY', 'DATASET',
]


# ── Master CSV ────────────────────────────────────────────────────────────────

def _load_master_csv() -> dict[str, dict[str, Optional[float]]]:
    """
    Returns {quarter_id: {code: value | None}} for every row in the master CSV.
    Returns an empty dict if the file does not exist.
    """
    path = Path(config.MASTER_CSV)
    if not path.exists():
        return {}

    rows: dict[str, dict[str, Optional[float]]] = {}
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = row.get('DATE') or row.get('date') or row.get('Quarter')
            if not qid:
                continue
            entry: dict[str, Optional[float]] = {}
            for code in config.COLUMN_CODES:
                raw = row.get(code, '')
                entry[code] = float(raw) if raw not in ('', None) else None
            rows[qid] = entry
    return rows


def _write_master_csv(rows: dict[str, dict[str, Optional[float]]]) -> None:
    """Write master CSV with two permanent header rows followed by data rows.

    Row 1 — column codes   (DATE, CPP.CASH..., …)
    Row 2 — descriptions   (blank DATE, 'CPP Consolidated …', …)
    Row 3+ — data          (2021-Q1, 14963, …)

    These two header rows must never change structure; they are always
    regenerated from config so they stay in sync with the codebase.
    """
    path = Path(config.MASTER_CSV)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ['DATE'] + config.COLUMN_CODES
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        # Row 1: codes header
        writer.writeheader()
        # Row 2: human-readable descriptions (DATE cell is blank)
        desc_row = {'DATE': ''}
        for code, desc in zip(config.COLUMN_CODES, config.COLUMN_DESCRIPTIONS):
            desc_row[code] = desc
        writer.writerow(desc_row)
        # Rows 3+: data
        for qid in sorted(rows.keys()):
            row = {'DATE': qid}
            for code in config.COLUMN_CODES:
                val = rows[qid].get(code)
                row[code] = '' if val is None else val
            writer.writerow(row)

    logger.info('Master CSV written: %s  (%d rows)', path, len(rows))


def update_master_csv(
    new_rows: dict[str, dict[str, int]],
) -> None:
    """
    Upsert new_rows into the master CSV.

    Parameters
    ----------
    new_rows : {quarter_id: {column_code: int_value}}
        Values are raw integers as extracted from the PDF (CAD millions).
    """
    existing = _load_master_csv()
    for qid, values in new_rows.items():
        if qid not in existing:
            existing[qid] = {c: None for c in config.COLUMN_CODES}
        for code, val in values.items():
            existing[qid][code] = val

    _write_master_csv(existing)
    logger.info('Master CSV updated with %d quarter(s): %s', len(new_rows), sorted(new_rows))


# ── Output helpers ────────────────────────────────────────────────────────────

def make_run_dir() -> Path:
    ts  = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    out = Path(config.OUTPUT_DIR) / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def _style_header_row(ws, row: int, ncols: int) -> None:
    for col in range(1, ncols + 1):
        cell            = ws.cell(row=row, column=col)
        cell.fill       = HEADER_FILL
        cell.font       = HEADER_FONT
        cell.alignment  = ALIGN_CENTER


def _auto_width(ws) -> None:
    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=8)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 2, 60)


# ── DATA workbook ─────────────────────────────────────────────────────────────

def _build_data_wb(rows: dict[str, dict[str, Optional[float]]]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'DATA'
    ws.freeze_panes = 'B3'   # freeze both header rows + date column

    # Row 1: column codes (A1 intentionally blank)
    ws.append([''] + config.COLUMN_CODES)
    _style_header_row(ws, 1, len(config.COLUMN_CODES) + 1)

    # Row 2: human-readable descriptions (A2 intentionally blank)
    ws.append([''] + config.COLUMN_DESCRIPTIONS)
    _style_header_row(ws, 2, len(config.COLUMN_DESCRIPTIONS) + 1)

    # Rows 3+: data
    for qid in sorted(rows.keys()):
        row_data = [qid]
        for code in config.COLUMN_CODES:
            val = rows[qid].get(code)
            row_data.append(val)
        ws.append(row_data)

    for row in ws.iter_rows(min_row=3):
        for cell in row:
            cell.font = BODY_FONT
            if cell.column == 1:
                cell.alignment = ALIGN_LEFT
            else:
                cell.alignment = ALIGN_RIGHT
                if cell.value is not None:
                    cell.number_format = '#,##0;(#,##0)'

    _auto_width(ws)
    return wb


# ── LATEST workbook ───────────────────────────────────────────────────────────

def _build_latest_wb(
    rows: dict[str, dict[str, Optional[float]]],
    latest_quarters: list[str],
) -> openpyxl.Workbook:
    """Two-row workbook: comparative quarter (row 3) + primary quarter (row 4).

    latest_quarters = [primary_qid, comparative_qid]  — in PDF column order.
    Written in reverse so the base period (comparative) sits above the current
    period (primary): e.g. 2026-Q1 on row 3, 2026-Q3 on row 4.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'LATEST'
    ws.freeze_panes = 'B3'

    ws.append([''] + config.COLUMN_CODES)
    _style_header_row(ws, 1, len(config.COLUMN_CODES) + 1)

    ws.append([''] + config.COLUMN_DESCRIPTIONS)
    _style_header_row(ws, 2, len(config.COLUMN_DESCRIPTIONS) + 1)

    for qid in reversed(latest_quarters):
        qdata = rows.get(qid, {})
        row_data = [qid]
        for code in config.COLUMN_CODES:
            row_data.append(qdata.get(code))
        ws.append(row_data)

    for row in ws.iter_rows(min_row=3):
        for cell in row:
            cell.font = BODY_FONT
            if cell.column == 1:
                cell.alignment = ALIGN_LEFT
            else:
                cell.alignment = ALIGN_RIGHT
                if cell.value is not None:
                    cell.number_format = '#,##0;(#,##0)'

    _auto_width(ws)
    return wb


# ── META workbook ─────────────────────────────────────────────────────────────

def _build_meta_wb() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'META'
    ws.freeze_panes = 'A2'

    ws.append(META_COLUMNS)
    _style_header_row(ws, 1, len(META_COLUMNS))

    for code, description in zip(config.COLUMN_CODES, config.COLUMN_DESCRIPTIONS):
        row: list = [code, description]
        for col in META_COLUMNS[2:]:
            row.append(config.META_TEMPLATE.get(col, ''))
        ws.append(row)

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font      = BODY_FONT
            cell.alignment = ALIGN_LEFT

    _auto_width(ws)
    return wb


# ── generate_outputs ──────────────────────────────────────────────────────────

def generate_outputs(
    run_dir: Optional[Path] = None,
    latest_quarters: Optional[list[str]] = None,
) -> Path:
    """
    Read master CSV and write DATA xlsx, LATEST xlsx (when latest_quarters
    supplied), META xlsx, and a ZIP into run_dir.
    Then mirror run_dir into output/latest/.

    Parameters
    ----------
    latest_quarters : [primary_qid, comparative_qid]
        The two quarter IDs from the most recently processed PDF.
        When None the LATEST file is omitted from the run.

    Returns the run_dir Path.
    """
    if run_dir is None:
        run_dir = make_run_dir()

    rows = _load_master_csv()
    if not rows:
        logger.warning('Master CSV is empty; outputs will be empty.')

    ts = run_dir.name  # e.g. '20261111_143022'

    data_name   = f'{config.OUTPUT_DATA_PREFIX}{ts}.xlsx'
    latest_name = f'{config.OUTPUT_LATEST_PREFIX}{ts}.xlsx'
    meta_name   = f'{config.OUTPUT_META_PREFIX}{ts}.xlsx'
    zip_name    = f'{config.OUTPUT_ZIP_PREFIX}{ts}.zip'

    data_path   = run_dir / data_name
    latest_path = run_dir / latest_name
    meta_path   = run_dir / meta_name
    zip_path    = run_dir / zip_name

    # Write DATA (all quarters combined)
    data_wb = _build_data_wb(rows)
    data_wb.save(data_path)
    logger.info('DATA xlsx → %s', data_path)

    # Write LATEST (most recent PDF's 2 quarters only)
    if latest_quarters:
        latest_wb = _build_latest_wb(rows, latest_quarters)
        latest_wb.save(latest_path)
        logger.info('LATEST xlsx → %s  quarters=%s', latest_path, latest_quarters)

    # Write META
    meta_wb = _build_meta_wb()
    meta_wb.save(meta_path)
    logger.info('META xlsx → %s', meta_path)

    # Write ZIP (include LATEST only when it was generated)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(data_path, data_name)
        if latest_quarters:
            zf.write(latest_path, latest_name)
        zf.write(meta_path, meta_name)
    logger.info('ZIP → %s', zip_path)

    # Sync to latest/
    latest_dir = Path(config.OUTPUT_DIR) / 'latest'
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(run_dir, latest_dir)
    logger.info('latest/ updated → %s', latest_dir)

    return run_dir
