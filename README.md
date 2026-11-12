# CANF_CPP RunBook

Automated pipeline that scrapes CPP Investments' quarterly financial statements website, downloads PDF reports, extracts portfolio data via text parsing, and maintains a master CSV + Excel outputs.

---

## What it does

CPP Investments publishes quarterly financial statement PDFs at:
`https://www.cppinvestments.com/the-fund/our-performance/financial-results/`

Each PDF contains a **Condensed Interim Consolidated Schedule of Investment Portfolio** table with two data columns:

- **Primary column** - the quarter being reported (e.g. Sep 30, 2026 → `2026-Q3`)
- **Comparative column** - the prior fiscal year-end (always March 31 → `YYYY-Q1`)

The pipeline extracts 23 investment portfolio line items from each PDF and writes them into a master CSV. From 17 quarterly PDFs (FY2022–present) it recovers 23 unique calendar quarters.

---

## Fiscal Year Calendar Mapping

CPP's fiscal year starts April 1. The CPP quarter numbers map to calendar quarters as:

| CPP Quarter | Period          | Calendar Quarter |
| ----------- | --------------- | ---------------- |
| Q1          | Apr–Jun (FY-1) | (FY-1)-Q2        |
| Q2          | Jul–Sep (FY-1) | (FY-1)-Q3        |
| Q3          | Oct–Dec (FY-1) | (FY-1)-Q4        |
| Comparative | Mar 31 (FY-1)   | (FY-1)-Q1        |

There is no Q4 quarterly PDF — the fiscal year-end (March 31) only appears as the comparative column in Q1–Q3 PDFs of the following year.

---

## Requirements

- Python 3.11+
- Google Chrome (auto-detected version)
- Dependencies:

```
pip install pymupdf requests beautifulsoup4 selenium undetected-chromedriver selenium-stealth openpyxl
```

---

## Usage

```bash
# Standard run — detects and backfills any missing quarters
python main.py

# Force full backfill regardless of state.json
python main.py --backfill

# Extract specific local PDFs without any scraping
python main.py --extract-only path/to/file.pdf

# Rebuild master CSV from all cached PDFs (no downloads, no Selenium)
python rebuild_master.py
```

---

## Folder Structure

```
CANF_CPP_RunBook/
├── main.py                        Entry point / CLI
├── config.py                      All constants, paths, column codes
├── orchestrator.py                Pipeline coordinator
├── scraper.py                     Selenium scraper + PDF downloader
├── extractor.py                   PDF text parser
├── file_generator.py              CSV + Excel output writer
├── rebuild_master.py              One-shot offline rebuild utility
│
├── Master_Data/
│   └── Master_CANF_CPP_DATA.csv   Master dataset (never delete manually)
│
├── downloads/
│   └── YYYYMMDD_HHMMSS/           One timestamped folder per pipeline run
│       └── YYYY_QN/
│           └── filename.pdf
│
├── output/
│   ├── YYYYMMDD_HHMMSS/           One timestamped folder per pipeline run
│   │   ├── CANF_CPP_DATA_<ts>.xlsx      All quarters combined
│   │   ├── CANF_CPP_LATEST_<ts>.xlsx    Most recent PDF's 2 quarters only
│   │   ├── CANF_CPP_META_<ts>.xlsx      Series metadata
│   │   └── CANF_CPP_<ts>.zip            All 3 xlsx files zipped
│   └── latest/                    Always a copy of the most recent run
│
├── state.json                     Tracks processed quarters (prevents re-download)
└── Project_information/           Sample files, reference docs
```

---

## Outputs

### Master CSV (`Master_Data/Master_CANF_CPP_DATA.csv`)

Fixed structure — the first two rows **never change**:

- **Row 1**: Column codes (`DATE, CPP.CASH.LEVEL.NONE.Q.1@CPP, ...`)
- **Row 2**: Human-readable descriptions (blank DATE cell, then full series names)
- **Row 3+**: Data rows, one per calendar quarter, sorted oldest-first

### DATA xlsx (`CANF_CPP_DATA_<ts>.xlsx`)

Full history — all quarters, same 2-row header structure as the master CSV. Numbers formatted with comma separators; negatives shown as `(value)` in accounting style. Freeze panes at B3.

### LATEST xlsx (`CANF_CPP_LATEST_<ts>.xlsx`)

Two data rows only — the primary quarter and comparative quarter from the most recently downloaded PDF. Same header structure. Useful for dashboard feeds or quick spot-checks.

### META xlsx (`CANF_CPP_META_<ts>.xlsx`)

One row per series code with metadata fields: frequency, multiplier (10⁶ = CAD millions), unit type, provider, country, etc.

---

## 23 Column Codes

| Code                                   | Description                                              |
| -------------------------------------- | -------------------------------------------------------- |
| CPP.CASH                               | Cash and cash equivalents                                |
| CPP.PRIVATEEQUITY                      | Private equities                                         |
| CPP.EQUITIES                           | Public equities                                          |
| CPP.LISTEDANDPRIVATEEQUITIES           | Total Equities                                           |
| CPP.BONDS                              | Bonds                                                    |
| CPP.OTHERDEBT                          | Other debt                                               |
| CPP.MONEYMARKET                        | Money market securities                                  |
| CPP.FIXEDINCOME                        | Total Debt (or Total Fixed Income in older PDFs)         |
| CPP.INVESTFUNDS                        | Investment Funds                                         |
| CPP.SECURITIESREPURCHASED              | Securities purchased under reverse repurchase agreements |
| CPP.DERIVATIVES                        | Derivative assets                                        |
| CPP.OTHERINVESTMENTRECEIVABLES         | Other investment receivables                             |
| CPP.TOTALINVESTMENTRECEIVABLES         | Total Investment Receivables and Other                   |
| CPP.TOTAL                              | Total Investments                                        |
| CPP.DEBTFINANCING                      | Debt financing liabilities                               |
| CPP.SECURITIESLOANSSOLDUNDERREPURCHASE | Securities and loans sold under repurchase agreements    |
| CPP.SECURITIESSOLDSHORT                | Securities sold short                                    |
| CPP.DERIVATIVELIABILITIES              | Derivative liabilities                                   |
| CPP.OTHERINVESTMENTLIABILITIES         | Other investment liabilities                             |
| CPP.TOTALINVESTMENTLIABILITIES         | Total investment liabilities                             |
| CPP.PENDINGTRADESRECEIVABLE            | Pending trades receivable                                |
| CPP.PENDINGTRADESPAYABLE               | Pending trades payable                                   |
| CPP.NETINVESTMENTS                     | Net investments                                          |

> All values in CAD millions (multiplier = 10⁶).

---

## Known Limitations

- **FY2022–FY2024 PDFs**: `CPP.INVESTFUNDS` (Investment Funds) has no equivalent row - CPP used different category names (`Absolute return strategies`, `Infrastructure`, `Real estate`) before introducing `Investment Funds`. Those cells are blank for pre-FY2024 quarters.
- **Cloudflare CDN**: PDFs are served from `cdn*.cppinvestments.com` with Cloudflare protection. The scraper uses a stealth Selenium session to capture cookies and passes them to `requests` for download. If `requests` gets an HTML challenge page it falls back to browser-triggered download.
- **Chrome cleanup error**: `OSError: [WinError 6] The handle is invalid` appears at shutdown - harmless Windows quirk where Chrome closes before Python's garbage collector calls `quit()` a second time.

---

## Backfill Detection

On every run the pipeline:

1. Reads all quarter IDs currently in the master CSV
2. Scrapes the CPP website to get all available quarterly PDF links
3. Downloads only the PDFs whose primary quarter is not already in the master CSV
4. Extracts both columns (primary + comparative) from each downloaded PDF
5. Upserts the extracted rows into the master CSV (existing data preserved)

Removing rows from the master CSV and re-running will trigger automatic re-download and re-extraction of only those missing quarters.
