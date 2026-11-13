"""
Extracts the "Condensed Interim Consolidated Schedule of Investment Portfolio"
table from CPP Investments quarterly financial statement PDFs.

Each PDF yields two data columns:
  col 0 — current quarter-end   (e.g. Sep 30, 2026 → '2026-Q3')
  col 1 — March 31 comparative  (e.g. Mar 31, 2026 → '2026-Q1')

Public API
----------
extract_pdf(pdf_path)
    → {'columns': ['2026-Q3', '2026-Q1'],
       'data': {'CPP.CASH.LEVEL.NONE.Q.1@CPP': [11446, 14963], ...}}

extract_all(pdf_paths, max_workers=4)
    → list of (pdf_path, result_dict | None)   (None = extraction failed)
"""

from __future__ import annotations

import re
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

import config

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SECTION_HEADERS: set[str] = {
    'equities',
    'debt',
    'fixed income',                    # FY2022–FY2024 PDFs (renamed to 'Debt' in later years)
    'investment receivables and other',
    'investment receivables',          # FY2022–FY2024 PDFs (renamed to '…and Other' later)
    'investment liabilities',
}

MONTH_TO_QUARTER: dict[int, str] = {3: 'Q1', 6: 'Q2', 9: 'Q3', 12: 'Q4'}

MONTH_NAMES: dict[str, int] = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}

_SUPERSCRIPT_RE = re.compile(r'[²³¹⁰-⁹]+')
_YEAR_RE        = re.compile(r'\b(20\d{2})\b')
_NUMBER_BODY_RE = re.compile(r'^\d+$')

# Standalone footnote markers: Unicode superscripts + common symbols used in
# financial PDFs (†dagger, ‡double-dagger, §section, *asterisk, •bullet).
# Expanded beyond digits to handle future CPP PDF format changes.
_FOOTNOTE_MARKER_RE = re.compile(r'^[¹²³⁰-⁹†‡§\*•]+$')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_footnotes(s: str) -> str:
    return _SUPERSCRIPT_RE.sub('', s).strip()


def _normalize(s: str) -> str:
    s = _strip_footnotes(s).strip().lower()
    # Strip trailing ASCII footnote digits (e.g. 'total investments1' → 'total investments')
    return re.sub(r'\d+$', '', s).strip()


def _is_number(s: str) -> bool:
    # Strip trailing ' $' that some PDFs merge with the preceding value
    body = s.replace(',', '').replace('(', '').replace(')', '').rstrip(' $')
    return bool(_NUMBER_BODY_RE.match(body)) and body != ''


def _parse_number(s: str) -> int:
    s = s.rstrip(' $')           # remove any trailing merged dollar sign
    body = s.replace(',', '')
    if s.startswith('(') and s.endswith(')'):
        return -int(body[1:-1])
    return int(body)


def _date_to_quarter_id(date_str: str) -> Optional[str]:
    """'September 30, 2026' → '2026-Q3'  |  'March 31, 2026³' → '2026-Q1'"""
    s = _strip_footnotes(date_str).lower()
    # Some older PDFs append an ASCII footnote digit directly after the year
    # e.g. 'March 31, 20222' → strip trailing digit to get '2022'
    s = re.sub(r'(20\d{2})\d+', r'\1', s)
    for month_name, month_num in MONTH_NAMES.items():
        if month_name in s:
            m = _YEAR_RE.search(s)
            if m:
                year = int(m.group(1))
                q    = MONTH_TO_QUARTER.get(month_num)
                if q:
                    return f'{year}-{q}'
    return None


# ── Core extraction ───────────────────────────────────────────────────────────

def _find_target_page(doc: fitz.Document) -> Optional[fitz.Page]:
    kw1, kw2 = config.TABLE_KEYWORDS
    for page in doc:
        text = ' '.join(page.get_text().lower().split())
        if kw1 in text and kw2 in text:
            return page
    return None


def _extract_column_dates(lines: list[str]) -> list[str]:
    """Return up to 2 date strings found after 'as at' headers."""
    dates: list[str] = []
    i = 0
    while i < len(lines) and len(dates) < 2:
        if lines[i].lower() == 'as at' and i + 1 < len(lines):
            dates.append(_strip_footnotes(lines[i + 1]))
            i += 2
        else:
            i += 1
    return dates


_DATA_START_MARKERS = {'equities', 'cash and cash equivalents'}

# Unicode superscript digits ¹²³ (U+00B9/B2/B3) and ⁰–⁹ (U+2070–2079)
_SUPERSCRIPT_DIGIT_RE = re.compile(r'^[¹²³⁰-⁹]$')


def _find_data_bounds(lines: list[str]) -> tuple[Optional[int], Optional[int]]:
    """Return (start, end) indices for the data section.

    start — first line of portfolio data (old PDFs begin with 'Cash and cash
            equivalents'; newer PDFs begin with 'Equities').
    end   — line that terminates the data block.  Formats handled:
            • standalone Unicode superscript:  '¹'
            • standalone footnote symbol:      '†', '‡', '§', '*', '•'
            • standalone ASCII digit:          '1'
            • digit/symbol + inline text:      '1 Consists of all the financial…'
            Fallback: if no footnote marker is found, end = len(lines) (page
            boundary) so the parser still runs rather than failing silently.
    """
    start = None
    for idx, line in enumerate(lines):
        if _normalize(line) in _DATA_START_MARKERS:
            start = idx
            break

    end = None
    for idx, line in enumerate(lines):
        # Case 1: standalone Unicode superscript or known footnote symbol
        if _SUPERSCRIPT_DIGIT_RE.match(line) or _FOOTNOTE_MARKER_RE.match(line):
            if idx + 1 < len(lines) and not _is_number(lines[idx + 1]):
                end = idx
                break

        # Case 2: ASCII digit or symbol leading an inline footnote sentence
        m = re.match(r'^([1-9†‡§\*•])\s*(.*)', line)
        if m:
            rest = m.group(2).strip()
            if rest == '':
                # standalone marker: confirm next line is not a data value
                if idx + 1 < len(lines) and not _is_number(lines[idx + 1]):
                    end = idx
                    break
            elif not _is_number(rest) and len(rest) > 10:
                # e.g. '1 Consists of all the financial…' — clearly a footnote
                end = idx
                break

    if end is None and start is not None:
        # No footnote marker found — use page boundary rather than hard-failing.
        # The parser will ignore any non-data trailing lines naturally.
        logger.warning(
            '_find_data_bounds: no footnote marker detected; '
            'using page end as data boundary — check PDF for new footnote format'
        )
        end = len(lines)

    return start, end


def _parse_rows(data_lines: list[str]) -> list[tuple[str, int, int]]:
    """
    State-machine parser for the label/value stream.
    Returns list of (normalized_label, col1_value, col2_value).

    'other' rows are tagged as 'other:<section>' (e.g. 'other:investment liabilities')
    so _map_rows can resolve the right code without fragile ordinal counting.
    Falls back to plain 'other' only if no section header has been seen yet.
    """
    rows: list[tuple[str, int, int]] = []
    current_parts:   list[str] = []
    pending_vals:    list[int]  = []
    current_section: str        = ''   # last section header seen

    for line in data_lines:
        if line == '$':
            continue

        if _is_number(line):
            pending_vals.append(_parse_number(line))
            if len(pending_vals) == 2:
                label = _normalize(' '.join(current_parts))
                # Tag 'other' with its section so _map_rows can resolve the
                # correct code without counting ordinal occurrences
                if label == 'other' and current_section:
                    label = f'other:{current_section}'
                rows.append((label, pending_vals[0], pending_vals[1]))
                current_parts = []
                pending_vals  = []
        else:
            if current_parts and not pending_vals:
                prev_norm = _normalize(' '.join(current_parts))
                if prev_norm in SECTION_HEADERS:
                    current_section = prev_norm   # update section tracker
                    current_parts   = [line]
                else:
                    current_parts.append(line)
            else:
                current_parts = [line]
                pending_vals  = []

    return rows


def _map_rows(rows: list[tuple[str, int, int]]) -> dict[str, list[int]]:
    """Map (label, v1, v2) triples → {code: [v1, v2]}.

    Handles two forms of 'other' labels:
      'other:<section>'  — section-tagged (preferred, produced by _parse_rows)
      'other'            — untagged fallback: resolve by ordinal count as before
    """
    data: dict[str, list[int]] = {}
    other_count  = 0   # fallback counter for plain 'other' (no section tag)
    unmatched: list[str] = []

    for label, v1, v2 in rows:
        if label.startswith('other:'):
            section = label.split(':', 1)[1]
            if 'receivable' in section:
                code = config.OTHER_ROW_CODES['investment_receivables']
            else:
                code = config.OTHER_ROW_CODES['investment_liabilities']
        elif label == 'other':
            # Fallback: section tracking did not fire (no section header seen yet)
            key  = 'investment_receivables' if other_count == 0 else 'investment_liabilities'
            code = config.OTHER_ROW_CODES[key]
            other_count += 1
            logger.debug(
                '_map_rows: untagged "other" resolved by count (%d) → %s', other_count, key
            )
        else:
            code = config.ROW_LABEL_TO_CODE.get(label)

        if code:
            data[code] = [v1, v2]
        else:
            unmatched.append(label)

    if unmatched:
        logger.warning('Unmatched PDF row labels: %s', unmatched)

    return data


# ── Extraction quality gate ───────────────────────────────────────────────────

def _validate_extraction_quality(pdf_name: str, data: dict) -> None:
    """
    Warn (never raise) when fewer than MIN_EXPECTED_CODES codes are populated.

    FY2022–FY2024 PDFs legitimately omit CPP.INVESTFUNDS, so the practical
    minimum is 22 for recent PDFs and 21 for older ones.  config.MIN_EXPECTED_CODES
    is set to 20 to accommodate both without false positives.
    """
    populated = len(data)
    total     = len(config.COLUMN_CODES)
    if populated < config.MIN_EXPECTED_CODES:
        logger.warning(
            'EXTRACTION QUALITY: %s — only %d/%d codes populated '
            '(threshold: %d).  PDF format may have changed.',
            pdf_name, populated, total, config.MIN_EXPECTED_CODES,
        )
    else:
        logger.info('%s — %d/%d codes populated', pdf_name, populated, total)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_pdf(pdf_path: str) -> dict:
    """
    Extract all 23 investment-portfolio values from a CPP quarterly PDF.

    Returns
    -------
    {
        'columns': ['2026-Q3', '2026-Q1'],   # quarter IDs, col0 then col1
        'data': {code: [col0_val, col1_val], ...}
    }

    Raises
    ------
    ValueError  if the target page or column dates cannot be found.
    """
    doc  = fitz.open(pdf_path)
    page = _find_target_page(doc)
    if page is None:
        raise ValueError(f'Target schedule page not found in {Path(pdf_path).name}')

    lines = [l.strip() for l in page.get_text().split('\n') if l.strip()]

    dates = _extract_column_dates(lines)
    if len(dates) < 2:
        raise ValueError(f'Could not extract 2 column dates from {Path(pdf_path).name}; found: {dates}')

    columns = [_date_to_quarter_id(d) for d in dates]
    if None in columns:
        raise ValueError(f'Failed to parse quarter IDs from dates {dates}')

    start, end = _find_data_bounds(lines)
    if start is None or end is None:
        raise ValueError(f'Could not locate data boundaries in {Path(pdf_path).name}')

    rows = _parse_rows(lines[start:end])
    data = _map_rows(rows)

    _validate_extraction_quality(Path(pdf_path).name, data)

    return {'columns': columns, 'data': data}


def _extract_worker(pdf_path: str) -> tuple[str, Optional[dict]]:
    """Worker function for ProcessPoolExecutor — returns (path, result|None)."""
    try:
        return pdf_path, extract_pdf(pdf_path)
    except Exception as exc:
        logger.error('Extraction failed for %s: %s', pdf_path, exc)
        return pdf_path, None


def extract_all(
    pdf_paths: list[str],
    max_workers: int = 4,
) -> list[tuple[str, Optional[dict]]]:
    """
    Extract all PDFs in parallel using a process pool.

    Returns list of (pdf_path, result_dict | None) in the same order as input.
    """
    if not pdf_paths:
        return []

    results: dict[str, Optional[dict]] = {}

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_extract_worker, p): p for p in pdf_paths}
        for future in as_completed(futures):
            path, result = future.result()
            results[path] = result

    return [(p, results[p]) for p in pdf_paths]
