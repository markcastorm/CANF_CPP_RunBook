# CLAUDE.md - CANF_CPP Pipeline Context

This file is the complete technical reference for Claude Code sessions working on this project. Read this instead of re-reading all source files.

---

## Project Purpose

Scrapes CPP Investments' quarterly financial statement PDFs, extracts 23 portfolio line items per quarter from text via a state-machine parser, and maintains a flat master CSV + three Excel outputs. The pipeline runs on Windows with Python 3.11 and uses Selenium (undetected_chromedriver) to bypass Cloudflare CDN protection.

---

## Architecture Overview

```
main.py
  └── orchestrator.py
        ├── scraper.py          (Selenium + requests download)
        ├── extractor.py        (PyMuPDF text parsing)
        └── file_generator.py   (CSV + xlsx output)

config.py                       (all constants — imported by every module)
rebuild_master.py               (standalone offline rebuild utility)
```

Data flow per run:

1. `scraper.load_existing_quarters()` - reads master CSV to find what's already there
2. `scraper.discover_and_download()` - scrapes CPP site, downloads only missing PDFs
3. `extractor.extract_all()` - parallel PDF extraction via ProcessPoolExecutor
4. `orchestrator._extraction_results_to_rows()` - flattens results, identifies latest PDF
5. `file_generator.update_master_csv()` - upserts rows into master CSV
6. `file_generator.generate_outputs()` - writes DATA, LATEST, META xlsx + ZIP

---

## File Map

| File                  | Responsibility                                                                    |
| --------------------- | --------------------------------------------------------------------------------- |
| `main.py`           | CLI entry point, argparse, `--backfill`, `--extract-only`                     |
| `config.py`         | Every constant: paths, URLs, column codes, row→code mapping, fiscal year mapping |
| `orchestrator.py`   | Coordinates the 5-step pipeline; `run()` and `backfill_run()`                 |
| `scraper.py`        | Selenium driver setup, page parsing, PDF download (requests + browser fallback)   |
| `extractor.py`      | PyMuPDF text extraction, state-machine parser, column→quarter ID mapping         |
| `file_generator.py` | Master CSV read/write, openpyxl workbook builders, output generation              |
| `rebuild_master.py` | Re-extracts all cached PDFs offline; rebuilds master from scratch                 |
| `state.json`        | Tracks processed quarters to prevent redundant downloads                          |

---

## config.py - Key Constants

### Paths

```python
BASE_DIR     = project root
DOWNLOAD_DIR = downloads/           # timestamped subdirs per run
OUTPUT_DIR   = output/              # timestamped subdirs + latest/
MASTER_DIR   = Master_Data/
MASTER_CSV   = Master_Data/Master_CANF_CPP_DATA.csv
STATE_FILE   = state.json
```

### Behaviour flags

```python
BACKFILL_ENABLED     = True   # True = find all gaps; False = only latest quarter
BACKFILL_MAX_WORKERS = 4      # parallel PDF extraction workers
SKIP_IF_PROCESSED    = True   # skip quarters already in state.json
START_FISCAL_YEAR    = 2022   # ignore PDFs older than this CPP FY
```

### Output prefixes

```python
OUTPUT_DATA_PREFIX   = 'CANF_CPP_DATA_'
OUTPUT_LATEST_PREFIX = 'CANF_CPP_LATEST_'
OUTPUT_META_PREFIX   = 'CANF_CPP_META_'
OUTPUT_ZIP_PREFIX    = 'CANF_CPP_'
```

### Fiscal year → calendar quarter mapping

```python
CPP_QUARTER_TO_CALENDAR_Q = {1: 'Q2', 2: 'Q3', 3: 'Q4'}
# CPP Q1 (Apr–Jun YYYY-1) → (YYYY-1)-Q2
# CPP Q2 (Jul–Sep YYYY-1) → (YYYY-1)-Q3
# CPP Q3 (Oct–Dec YYYY-1) → (YYYY-1)-Q4
# Comparative is always Mar 31 (YYYY-1) → (YYYY-1)-Q1
```

### COLUMN_CODES / COLUMN_DESCRIPTIONS

23 parallel lists in `config.py`. Order is authoritative - the master CSV columns follow this order exactly. Do not reorder. Codes use the format `CPP.<SERIES>.LEVEL.NONE.Q.1@CPP`.

### ROW_LABEL_TO_CODE

Dict mapping normalized PDF row label text → column code. Contains multiple alternate phrasings for the same code to handle format changes across FY2022–FY2027. Key alternates:

- `'total fixed income'` → same code as `'total debt'` (FY2022–FY2024 section name)
- `'investment receivables'` → same code as `'investment receivables and other'`
- Multiple phrasings for `CPP.SECURITIESLOANSSOLDUNDERREPURCHASE` (FY2022 Q1/Q2 had truncated text)

### OTHER_ROW_CODES

`'other'` appears twice in each PDF (once in receivables, once in liabilities). Resolved by section tracking in `_parse_rows` — the parser emits `'other:investment receivables and other'` or `'other:investment liabilities'` depending on the last section header seen. `_map_rows` resolves to the right code from the tag. Ordinal-count fallback is kept for edge cases.

```python
OTHER_ROW_CODES = {
    'investment_receivables': 'CPP.OTHERINVESTMENTRECEIVABLES...',
    'investment_liabilities': 'CPP.OTHERINVESTMENTLIABILITIES...',
}
```

### Extraction quality thresholds

```python
MIN_EXPECTED_CODES = 20  # warn if fewer codes populated per quarter (of 23 total)
MIN_EXPECTED_LINKS = 3   # raise RuntimeError if fewer quarterly links found on CPP website
```

---

## main.py

Simple argparse wrapper. Three modes:

- **Default**: calls `orchestrator.run()`
- **`--backfill`**: calls `orchestrator.backfill_run()`
- **`--extract-only <pdf> [pdf...]`**: calls `extractor.extract_all()` then `file_generator.update_master_csv()` directly — no Selenium. Correctly unpacks `(new_rows, latest_quarters)` from `_extraction_results_to_rows` so the LATEST xlsx is generated.

---

## orchestrator.py

### `run()`

Main pipeline entry. Steps:

1. `run_dir = file_generator.make_run_dir()` - creates `output/YYYYMMDD_HHMMSS/`
2. `existing_quarters = scraper.load_existing_quarters()` - reads master CSV; saves `prior_count` for health summary
3. If `BACKFILL_ENABLED`: `scraper.discover_and_download(existing_quarters)` else `scraper.download()`
4. `extractor.extract_all(pdf_paths, max_workers=...)` - parallel extraction; logs warning if any PDF returned `None`
5. `new_rows, latest_quarters = _extraction_results_to_rows(results)` - flatten + find latest PDF
6. `file_generator.update_master_csv(new_rows)` - upsert to master
7. `scraper.mark_quarter_processed(...)` - write to state.json per PDF
8. `file_generator.generate_outputs(run_dir, latest_quarters=latest_quarters)`
9. `_run_health_summary(...)` - structured log block at end of every run

### `backfill_run()`

Temporarily sets `config.BACKFILL_ENABLED = True` then calls `run()`.

### `_extraction_results_to_rows(results) → (rows_dict, latest_quarters)`

**Returns a tuple** — do not unpack as a single value.

- `results` = list of `(pdf_path, result | None)` from `extractor.extract_all()`
- Flattens both columns (primary + comparative) of every PDF into `{qid: {code: value}}`
- Identifies the PDF with the highest primary quarter ID as "latest"
- Returns `latest_quarters = [primary_qid, comparative_qid]` for that PDF

### `_run_health_summary(prior_existing, new_rows, pdf_infos, results, latest_quarters, run_dir)`

Logs a structured summary block at the end of every run:

```
==============================================================
CANF_CPP PIPELINE RUN SUMMARY
  PDFs downloaded      : 2
  PDFs extracted       : 2 / 2 succeeded
  New quarters added   : ['2026-Q2', '2026-Q3']
  Existing qtrs updated: ['2026-Q1']
  Code coverage  [OK  ] 2026-Q3 : 23/23 codes
  Master CSV quarters  : 23  (was 21, +2)
  LATEST quarters      : primary=2026-Q3  comparative=2026-Q1
  Status               : SUCCESS   ← or PARTIAL if any failure/low coverage
==============================================================
```

- `Status: PARTIAL` if any PDF failed extraction or any quarter is below `MIN_EXPECTED_CODES`
- Detects and logs `DATA LOSS` if master CSV quarter count decreases during a run

---

## scraper.py

### Browser setup

`_build_driver(download_dir)` - creates `undetected_chromedriver` with:

- Headless mode (configurable via `config.HEADLESS_MODE`)
- `selenium_stealth` to mask automation fingerprints
- CDP `Page.setDownloadBehavior` to control download directory
- **Important**: `download_dir` must be a `str`, not a `Path` - Chrome's JSON prefs serialization fails on `WindowsPath`

### Page parsing - `_collect_quarterly_links(page_source)`

Multi-strategy dispatcher. Tries each strategy in order and uses the first that returns results. Raises `RuntimeError` if all three fail (triggers hard pipeline failure rather than silent empty run). Warns if fewer than `config.MIN_EXPECTED_LINKS` links found.

**Strategy 1 — `_links_structural(soup)`** (primary)
- Relies on `<div class="fiscal-wrap" id="fiscal-YYYY">` and `<div class="moreInfo">`
- Fastest and most precise; fails if CPP redesigns IR page CSS/IDs

**Strategy 2 — `_links_text_search(soup)`** (CSS-agnostic fallback)
- Scans ALL `<a href="*.pdf">` tags on the page regardless of container
- Matches anchor text against `"1st|2nd|3rd quarter fiscal YYYY"` (case-insensitive)
- Survives any CSS class or ID rename

**Strategy 3 — `_links_broad_pdf_scan(soup)`** (broadest fallback)
- Also accepts `"first/second/third quarter fiscal YYYY"` text variants
- Falls back to parsing the PDF URL filename via `_parse_quarter_from_url(url)`
  - Handles patterns like `Q2-F27-...pdf` → `(2, 2027)`, `Q1-F2027-...pdf` → `(1, 2027)`
- Survives link text format changes AND CSS restructuring simultaneously

Each fallback logs a `WARNING` so you know the site has changed. Strategy 1 logs `INFO`.

### Download strategy - `_download_pdf(url, dest_path, driver, cdn_cookies)`

Two-stage:

1. **Fast path**: `requests.get()` with CDN session cookies. If response `Content-Type` is `text/html` (Cloudflare challenge), returns `False`.
2. **Browser fallback**: `driver.execute_cdp_cmd('Page.setDownloadBehavior', ...)` then `driver.get(url)`. Polls `dest_dir` for a stable `.pdf` file (ignores `.crdownload`).

### Quarter ID helpers

- `_parse_quarter_link(text)` → `(cpp_q_num, cpp_fy)` or `(None, None)` if not a quarterly FS link
- `_quarter_ids(cpp_q_num, cpp_fy)` → `(current_quarter_id, comparative_quarter_id)`
  - `current = f'{cpp_fy-1}-{CPP_QUARTER_TO_CALENDAR_Q[cpp_q_num]}'`
  - `comparative = f'{cpp_fy-1}-Q1'` (always March 31 of prior year)

### State management

- `state.json` stores `{"processed_quarters": {"2026-Q3": {"processed_at": ..., "pdf_path": ..., "cpp_fy": ..., "cpp_quarter": ...}}}`
- `mark_quarter_processed(quarter_id, pdf_path, cpp_fy, cpp_quarter)` — writes to state.json
- `_quarter_already_processed(quarter_id)` — reads state.json

### `load_existing_quarters()`

Reads master CSV directly (bypasses `file_generator._load_master_csv`) using `csv.reader`. Skips rows `i < 2` (codes header + descriptions header). Returns `set` of quarter IDs that have at least one non-empty value.

### `download()` vs `discover_and_download()`

- `download()` — skips quarters in `state.json`, used for normal (non-backfill) runs
- `discover_and_download(existing_quarters)` — skips quarters in `existing_quarters` (from master CSV), used for backfill. Creates download dir under `config.DOWNLOAD_DIR` (not `output/`).

### `_fetch_all_links(run_dir)`

Shared helper used by both `download()` and `discover_and_download()`. Opens one Selenium session, loads the page, closes the driver, returns all quarterly links.

---

## extractor.py

### Entry points

- `extract_pdf(pdf_path)` → `{'columns': ['2026-Q3', '2026-Q1'], 'data': {code: [v0, v1], ...}}`
- `extract_all(pdf_paths, max_workers=4)` → `[(path, result|None), ...]` via `ProcessPoolExecutor`

### Pipeline inside `extract_pdf`

1. `_find_target_page(doc)` — scans pages for both `config.TABLE_KEYWORDS`
2. `_extract_column_dates(lines)` — finds lines following `'as at'` headers; returns 2 date strings
3. `_date_to_quarter_id(date_str)` × 2 — converts "September 30, 2026" → `'2026-Q3'`
4. `_find_data_bounds(lines)` → `(start, end)` — locates the data block
5. `_parse_rows(lines[start:end])` → `[(label, v1, v2), ...]` — state-machine parser
6. `_map_rows(rows)` → `{code: [v1, v2]}` — maps labels to column codes

### `_find_data_bounds(lines)` — critical function

Finds where data starts and ends in the line stream.

**Start**: first line whose `_normalize()` is in `{'equities', 'cash and cash equivalents'}`.

- Newer PDFs start with `'Equities'` section header
- Some older PDFs start data with `'Cash and cash equivalents'`

**End**: first footnote marker after data. Formats handled (in order of check):

1. Standalone Unicode superscript: `'¹'` — matched by `_SUPERSCRIPT_DIGIT_RE`
2. Standalone footnote symbol: `'†'`, `'‡'`, `'§'`, `'*'`, `'•'` — matched by `_FOOTNOTE_MARKER_RE`
3. Standalone ASCII digit: `'1'` — confirmed by next line not being a number
4. Digit/symbol + inline text: `'1 Consists of all the financial...'` — marker + 10+ char sentence

**Fallback**: if no footnote marker found after exhausting all patterns, `end = len(lines)` (page boundary) and a `WARNING` is logged. The parser still runs rather than raising `ValueError` and dropping the whole PDF.

### `_parse_rows(lines)` — state machine

Processes the line stream between start and end bounds:

- Skips `'$'` lines (dollar sign headers that appear in some PDFs)
- Accumulates label lines in `current_parts` until 2 numbers are seen
- When 2 numbers found: `label = _normalize(' '.join(current_parts))`, emit `(label, v1, v2)`
- If a new non-number line arrives while `current_parts` holds a known section header, starts fresh and updates `current_section`
- **Section tracking**: `current_section` is updated whenever a `SECTION_HEADERS` label is detected. When the label is `'other'`, it is tagged as `f'other:{current_section}'` before emission (e.g. `'other:investment liabilities'`). This makes `_map_rows` section-aware rather than relying on fragile ordinal counting.

### `_map_rows(rows)` — label → code mapping

Handles two forms of `'other'` label:

- `'other:<section>'` — preferred; resolves from tag: `'receivable'` in section → `CPP.OTHERINVESTMENTRECEIVABLES`, else → `CPP.OTHERINVESTMENTLIABILITIES`
- `'other'` — untagged fallback: ordinal count (first = receivables, second = liabilities)

### `_validate_extraction_quality(pdf_name, data)`

Called at the end of every `extract_pdf()`. Warns (never raises) if `len(data) < config.MIN_EXPECTED_CODES`. Normal values: 22/23 for FY2022–FY2024 (CPP.INVESTFUNDS blank by design), 23/23 for FY2025+.

### Number handling

- `_is_number(s)` — strips trailing `' $'` (some FY2026 PDFs merge the dollar sign with the preceding value), strips commas and parens, checks for all-digit body
- `_parse_number(s)` — strips trailing `' $'`, handles `(value)` as negative
- `_normalize(s)` — strips Unicode superscripts, lowercases, strips trailing ASCII digits (some PDFs had `'total investments1'`)

### `_date_to_quarter_id`

- Strips Unicode superscripts
- Applies `re.sub(r'(20\d{2})\d+', r'\1', s)` to fix PDFs that had `'March 31, 20222'` (ASCII digit appended after year)

### Known extraction gaps

FY2022–FY2024 PDFs (22/23 fields): `CPP.INVESTFUNDS` is always blank. CPP used different category names before introducing "Investment Funds":

- Absolute return strategies
- Infrastructure / Real assets infrastructure
- Real estate
- Sustainable energies
  These are logged as `Unmatched PDF row labels` warnings — expected, not a bug.

---

## file_generator.py

### Master CSV structure — NEVER CHANGE

```
Row 1: DATE,CPP.CASH.LEVEL.NONE.Q.1@CPP,...   ← codes header
Row 2: ,CPP Consolidated Schedule of..., ...  ← descriptions header (DATE cell blank)
Row 3: 2021-Q1,14532,158589,...               ← data rows sorted oldest-first
...
Row N: 2026-Q3,...
```

Both header rows are regenerated from `config.COLUMN_CODES` / `config.COLUMN_DESCRIPTIONS` on every write, so they always stay in sync with the codebase.

### `_load_master_csv()` → `{qid: {code: value|None}}`

Uses `csv.DictReader` (row 1 becomes fieldnames). The descriptions row (row 2) has `DATE=''` → skipped by `if not qid: continue`. Returns float values or `None` for empty cells.

### `_write_master_csv(rows)`

Writes: row 1 (codes via `writer.writeheader()`), row 2 (descriptions as a data row with `DATE=''`), then all data rows sorted by quarter ID.

### `update_master_csv(new_rows)`

Load → merge → write. Existing quarters are preserved; new values upsert by code.

### `generate_outputs(run_dir=None, latest_quarters=None)`

Orchestrates all output files:

1. `_build_data_wb(rows)` → `CANF_CPP_DATA_<ts>.xlsx` (all quarters)
2. `_build_latest_wb(rows, latest_quarters)` → `CANF_CPP_LATEST_<ts>.xlsx` (2 rows only) — only if `latest_quarters` is provided
3. `_build_meta_wb()` → `CANF_CPP_META_<ts>.xlsx`
4. ZIP containing all three
5. Mirror run_dir to `output/latest/`

### Workbook styling constants

```python
HEADER_FILL  = PatternFill('solid', fgColor='1F4E79')   # dark navy blue
HEADER_FONT  = Font(color='FFFFFF', bold=True, name='Calibri', size=10)
BODY_FONT    = Font(name='Calibri', size=10)
```

Number format for data cells: `'#,##0;(#,##0)'`

- Positive: `1,109,356`
- Negative: `(114,777)` — accounting style matching CPP's own PDFs

### `_build_data_wb(rows)` and `_build_latest_wb(rows, latest_quarters)`

Both use the same 2-row header + data row structure. `_build_latest_wb` only writes `len(latest_quarters)` data rows (always 2). Freeze panes at `B3` in both.

### `make_run_dir()` → `Path`

Creates `output/YYYYMMDD_HHMMSS/`. Returns a `Path` object — callers that pass this to scraper functions must convert to `str`.

---

## rebuild_master.py

Standalone script for offline full rebuild. Use when:

- Master CSV is corrupted or deleted
- Needs regeneration from cached PDFs
- Testing after extractor changes

Finds all PDFs under `output/20261112_100731/` (the first full run), deduplicates by filename, runs extraction, then calls `update_master_csv` and `generate_outputs`. Passes `latest_quarters` correctly.

**When to run**: `python rebuild_master.py`

---

## Directory Conventions

### downloads/

One timestamped subdirectory per pipeline run. Inside, one subdirectory per quarter: `YYYY_QN/filename.pdf`.

```
downloads/
└── 20261112_133449/
    ├── 2026_Q2/Q1-F27-Financial-Statements-English.pdf
    └── 2026_Q3/Q2-F27-Financial-Statements-November-10-2026.pdf
```

### output/

Same timestamped structure, but for Excel outputs.

```
output/
├── 20261112_104813/
│   ├── CANF_CPP_DATA_20261112_104813.xlsx
│   ├── CANF_CPP_LATEST_20261112_104813.xlsx
│   ├── CANF_CPP_META_20261112_104813.xlsx
│   └── CANF_CPP_20261112_104813.zip
└── latest/                           ← always a copy of the most recent run
```

---

## Common Failure Modes

### `_find_data_bounds: no footnote marker detected` (WARNING in logs)

New footnote symbol not in `_FOOTNOTE_MARKER_RE`. The parser falls back to using the page boundary as `end` and continues — extraction still runs. Check the log for `Unmatched PDF row labels` to see if data was affected. If a new symbol is introduced, add it to `_FOOTNOTE_MARKER_RE` in `extractor.py`.

### `EXTRACTION QUALITY: only N/23 codes populated` (WARNING in logs)

Fewer codes than `MIN_EXPECTED_CODES = 20`. Either a new PDF format introduced new label text (add to `ROW_LABEL_TO_CODE`) or `_find_data_bounds` chose an incorrect boundary (inspect via `--extract-only`).

### `All HTML parsing strategies returned 0 quarterly links` (RuntimeError — hard stop)

All three scraper strategies failed. The CPP website HTML has changed in an unrecognised way. Inspect the page source and update `_links_structural()` in `scraper.py`. Strategies 2 and 3 should catch most changes automatically, so this error means even the PDF URL filename pattern changed.

### `ValueError: Could not extract 2 column dates`

Target page found but `'as at'` header pattern missing or PDF layout changed. Run `python main.py --extract-only <path>` and add debug prints to `_extract_column_dates`.

### `'March 31, 20222'` parse failure

ASCII digit appended after year in older PDFs. Already handled by `re.sub(r'(20\d{2})\d+', r'\1', s)` in `_date_to_quarter_id`.

### `'242,092 $'` not recognised as number

Dollar sign from col2 merged with col1 value. Already handled by `.rstrip(' $')` in `_is_number` and `_parse_number`.

### Downloads going to `output/` instead of `downloads/`

Caused by passing `run_dir` from orchestrator to `scraper.discover_and_download()`. Fixed — the scraper creates its own timestamped dir under `config.DOWNLOAD_DIR`.

### `TypeError: Object of type WindowsPath is not JSON serializable`

`make_run_dir()` returns a `Path`. Chrome's CDP `Page.setDownloadBehavior` requires a plain `str`. Fixed with `download_dir = str(download_dir)` in `_build_driver` and `run_dir = str(run_dir)` in `_run_scrape_session`.

### `OSError: [WinError 6] The handle is invalid` at shutdown

Harmless. Chrome already closed before Python's garbage collector calls `driver.quit()` a second time via `__del__`. Not worth fixing.

### Master CSV shows only N rows after a run that should have kept existing rows

`_load_master_csv()` reads with `csv.DictReader` — if the CSV was corrupted (e.g. PowerShell `Out-File` joined all lines into one), DictReader returns nothing. Run `rebuild_master.py` to restore from cached PDFs.

---

## Multi-Era PDF Compatibility

CPP changed the portfolio structure between FY2024 and FY2025:

| Section             | FY2022–FY2024 name                                     | FY2025+ name                     |
| ------------------- | ------------------------------------------------------- | -------------------------------- |
| Debt section        | Fixed income                                            | Debt                             |
| Receivables section | Investment receivables                                  | Investment receivables and Other |
| Sub-asset classes   | Absolute return strategies, Infrastructure, Real estate | Investment funds                 |

Handled via:

- `SECTION_HEADERS` includes both old and new names
- `ROW_LABEL_TO_CODE` has alternate entries for old phrasings
- `CPP.INVESTFUNDS` is blank for pre-FY2024 quarters (old categories have no 1:1 mapping)

---

## Adding a New Column Code

1. Add the new code to `config.COLUMN_CODES` (append at end to preserve existing column order)
2. Add the description to `config.COLUMN_DESCRIPTIONS` at the same index
3. Add all PDF row label phrasings to `config.ROW_LABEL_TO_CODE`
4. Run `python rebuild_master.py` to regenerate the master CSV with the new column

---

## Testing Backfill Detection

```python
# Remove last N quarters from master CSV
python -c "
csv_path = r'Master_Data/Master_CANF_CPP_DATA.csv'
with open(csv_path, encoding='utf-8', newline='') as f:
    lines = f.readlines()
# Keep 2 headers + (total - N) data rows
with open(csv_path, 'w', encoding='utf-8', newline='') as f:
    f.writelines(lines[:2 + len(lines) - 2 - N])
"

# Then run the pipeline — it should detect and re-download only the removed quarters
python main.py
```
