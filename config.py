import os

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
OUTPUT_DIR   = os.path.join(BASE_DIR, 'output')
MASTER_DIR   = os.path.join(BASE_DIR, 'Master_Data')
MASTER_CSV   = os.path.join(MASTER_DIR, 'Master_CANF_CPP_DATA.csv')
STATE_FILE   = os.path.join(BASE_DIR, 'state.json')

# ── Source ────────────────────────────────────────────────────────────────────
BASE_URL = 'https://www.cppinvestments.com/the-fund/our-performance/financial-results/'

# ── Browser ───────────────────────────────────────────────────────────────────
HEADLESS_MODE  = True
WAIT_TIMEOUT   = 60
PAGE_LOAD_WAIT = 5
CLICK_DELAY    = (2, 4)

# ── Backfill ──────────────────────────────────────────────────────────────────
# START_FISCAL_YEAR: collect quarterly PDFs starting from this CPP fiscal year.
# CPP fiscal year 2022 covers April 2021 – March 2022, so its Q1 is June 30, 2021.
START_FISCAL_YEAR = 2022

# BACKFILL_ENABLED: True  = discover all missing quarters from master CSV and
#                           process them in one run.
#                  False  = process only the single latest available quarter.
BACKFILL_ENABLED     = True
BACKFILL_MAX_WORKERS = 4

# SKIP_IF_PROCESSED: True  = skip the run when the latest quarter was already
#                            recorded in state.json.
#                   False  = bypass the check and always re-run.
SKIP_IF_PROCESSED = True

# ── Dataset identifiers ────────────────────────────────────────────────────────
DATASET_NAME       = 'CANF_CPP'
OUTPUT_DATA_PREFIX   = 'CANF_CPP_DATA_'
OUTPUT_LATEST_PREFIX = 'CANF_CPP_LATEST_'
OUTPUT_META_PREFIX   = 'CANF_CPP_META_'
OUTPUT_ZIP_PREFIX    = 'CANF_CPP_'

# ── CPP Fiscal Year Quarter → Calendar Quarter mapping ─────────────────────────
# CPP fiscal year starts April 1; year-end is March 31 of the fiscal year number.
#   Q1 (Apr–Jun YYYY-1) → period ends Jun 30, YYYY-1  → calendar (YYYY-1)-Q2
#   Q2 (Jul–Sep YYYY-1) → period ends Sep 30, YYYY-1  → calendar (YYYY-1)-Q3
#   Q3 (Oct–Dec YYYY-1) → period ends Dec 31, YYYY-1  → calendar (YYYY-1)-Q4
#   Annual (Jan–Mar)    → period ends Mar 31, YYYY     → calendar  YYYY    -Q1
CPP_QUARTER_TO_CALENDAR_Q = {1: 'Q2', 2: 'Q3', 3: 'Q4'}

# ── Absolute column codes (CSV column order is authoritative) ─────────────────
# These codes are stored in the EXACT ORDER they appear as headers in the
# master CSV.  Do NOT reorder.

COLUMN_CODES = [
    'CPP.CASH.LEVEL.NONE.Q.1@CPP',
    'CPP.PRIVATEEQUITY.LEVEL.NONE.Q.1@CPP',
    'CPP.EQUITIES.LEVEL.NONE.Q.1@CPP',
    'CPP.LISTEDANDPRIVATEEQUITIES.LEVEL.NONE.Q.1@CPP',
    'CPP.BONDS.LEVEL.REPORTED.Q.1@CPP',
    'CPP.OTHERDEBT.LEVEL.NONE.Q.1@CPP',
    'CPP.MONEYMARKET.LEVEL.NONE.Q.1@CPP',
    'CPP.FIXEDINCOME.LEVEL.NONE.Q.1@CPP',
    'CPP.INVESTFUNDS.LEVEL.NONE.Q.1@CPP',
    'CPP.SECURITIESREPURCHASED.LEVEL.NONE.Q.1@CPP',
    'CPP.DERIVATIVES.LEVEL.NONE.Q.1@CPP',
    'CPP.OTHERINVESTMENTRECEIVABLES.LEVEL.NONE.Q.1@CPP',
    'CPP.TOTALINVESTMENTRECEIVABLES.LEVEL.NONE.Q.1@CPP',
    'CPP.TOTAL.LEVEL.NONE.Q.1@CPP',
    'CPP.DEBTFINANCING.LEVEL.NONE.Q.1@CPP',
    'CPP.SECURITIESLOANSSOLDUNDERREPURCHASE.LEVEL.NONE.Q.1@CPP',
    'CPP.SECURITIESSOLDSHORT.LEVEL.NONE.Q.1@CPP',
    'CPP.DERIVATIVELIABILITIES.LEVEL.NONE.Q.1@CPP',
    'CPP.OTHERINVESTMENTLIABILITIES.LEVEL.NONE.Q.1@CPP',
    'CPP.TOTALINVESTMENTLIABILITIES.LEVEL.NONE.Q.1@CPP',
    'CPP.PENDINGTRADESRECEIVABLE.LEVEL.NONE.Q.1@CPP',
    'CPP.PENDINGTRADESPAYABLE.LEVEL.NONE.Q.1@CPP',
    'CPP.NETINVESTMENTS.LEVEL.NONE.Q.1@CPP',
]

COLUMN_DESCRIPTIONS = [
    'CPP Consolidated Schedule of Investment Portfolio, Cash and cash equivalents',
    'CPP Consolidated Schedule of Investment Portfolio, Private equities',
    'CPP Consolidated Schedule of Investment Portfolio, Public equities',
    'CPP Consolidated Schedule of Investment Portfolio, Total Equities',
    'CPP Consolidated Schedule of Investment Portfolio, Bonds',
    'CPP Consolidated Schedule of Investment Portfolio, Other debt',
    'CPP Consolidated Schedule of Investment Portfolio, Money market securities',
    'CPP Consolidated Schedule of Investment Portfolio, Total Debt',
    'CPP Consolidated Schedule of Investment Portfolio, Investment Funds',
    'CPP Consolidated Schedule of Investment Portfolio, Securities purchased under reverse repurchase agreements and cash collateral pledged under securities borrowed',
    'CPP Consolidated Schedule of Investment Portfolio, Derivative assets',
    'CPP Consolidated Schedule of Investment Portfolio, Other Investment receivables',
    'CPP Consolidated Schedule of Investment Portfolio, Total Investment Recievables and Other',
    'CPP Consolidated Schedule of Investment Portfolio, Total Investments',
    'CPP Consolidated Schedule of Investment Portfolio, Debt financing liabilities',
    'CPP Consolidated Schedule of Investment Portfolio, Securities and loans sold under repurchase agreements and cash collateral',
    'CPP Consolidated Schedule of Investment Portfolio, Securities sold short',
    'CPP Consolidated Schedule of Investment Portfolio, Derivative liabilities',
    'CPP Consolidated Schedule of Investment Portfolio, Other investment liabilities',
    'CPP Consolidated Schedule of Investment Portfolio, Total investment liabilities',
    'CPP Consolidated Schedule of Investment Portfolio, Pending trades receivable',
    'CPP Consolidated Schedule of Investment Portfolio, Pending trades payable',
    'CPP Consolidated Schedule of Investment Portfolio, Net investments',
]

# ── PDF row label → column code mapping ───────────────────────────────────────
# Maps normalized (stripped, lowercased) row labels extracted from the PDF to
# their corresponding column code.  Labels are absolute and must NOT be changed
# unless CPP changes its published table structure.
#
# The table order in the PDF differs from the CSV column order; the extractor
# uses this dict to map each label to the correct code, then emits values in
# COLUMN_CODES order.

ROW_LABEL_TO_CODE = {
    # ── Equities ──────────────────────────────────────────────────────────────
    'public equities':
        'CPP.EQUITIES.LEVEL.NONE.Q.1@CPP',
    'private equities':
        'CPP.PRIVATEEQUITY.LEVEL.NONE.Q.1@CPP',
    'total equities':
        'CPP.LISTEDANDPRIVATEEQUITIES.LEVEL.NONE.Q.1@CPP',

    # ── Debt ──────────────────────────────────────────────────────────────────
    'bonds':
        'CPP.BONDS.LEVEL.REPORTED.Q.1@CPP',
    'other debt':
        'CPP.OTHERDEBT.LEVEL.NONE.Q.1@CPP',
    'money market securities':
        'CPP.MONEYMARKET.LEVEL.NONE.Q.1@CPP',
    'total debt':
        'CPP.FIXEDINCOME.LEVEL.NONE.Q.1@CPP',
    # FY2022–FY2024 PDFs called this section 'Fixed income' (not 'Debt')
    'total fixed income':
        'CPP.FIXEDINCOME.LEVEL.NONE.Q.1@CPP',

    # ── Investment funds ──────────────────────────────────────────────────────
    'investment funds':
        'CPP.INVESTFUNDS.LEVEL.NONE.Q.1@CPP',

    # ── Investment receivables and Other ──────────────────────────────────────
    'securities purchased under reverse repurchase agreements and cash collateral pledged on securities borrowed':
        'CPP.SECURITIESREPURCHASED.LEVEL.NONE.Q.1@CPP',
    # FY2022 Q1/Q2 PDFs use a truncated single-line label (second half absent from PDF)
    'securities purchased under reverse repurchase agreements and':
        'CPP.SECURITIESREPURCHASED.LEVEL.NONE.Q.1@CPP',
    'derivative assets':
        'CPP.DERIVATIVES.LEVEL.NONE.Q.1@CPP',
    # 'other' in this section → CPP.OTHERINVESTMENTRECEIVABLES (resolved by context)
    'total investment receivables and other':
        'CPP.TOTALINVESTMENTRECEIVABLES.LEVEL.NONE.Q.1@CPP',
    # FY2022–FY2024 PDFs omit 'and Other' from the section total
    'total investment receivables':
        'CPP.TOTALINVESTMENTRECEIVABLES.LEVEL.NONE.Q.1@CPP',

    # ── Total investments (footnote superscripts stripped by extractor) ────────
    'total investments':
        'CPP.TOTAL.LEVEL.NONE.Q.1@CPP',

    # ── Investment liabilities ────────────────────────────────────────────────
    'debt financing liabilities':
        'CPP.DEBTFINANCING.LEVEL.NONE.Q.1@CPP',
    # Primary phrasing as seen in Q2 F27 PDF:
    'securities and loans sold under repurchase agreements and cash collateral received on securities lent':
        'CPP.SECURITIESLOANSSOLDUNDERREPURCHASE.LEVEL.NONE.Q.1@CPP',
    # Alternate shorter phrasing seen in some PDFs:
    'securities and loans sold under repurchase agreements and cash collateral':
        'CPP.SECURITIESLOANSSOLDUNDERREPURCHASE.LEVEL.NONE.Q.1@CPP',
    # FY2022 Q1 phrasing (omits 'and loans', omits 'received on securities lent'):
    'securities sold under repurchase agreements and cash collateral':
        'CPP.SECURITIESLOANSSOLDUNDERREPURCHASE.LEVEL.NONE.Q.1@CPP',
    # FY2022 Q2 phrasing (omits 'and loans'):
    'securities sold under repurchase agreements and cash collateral received on securities lent':
        'CPP.SECURITIESLOANSSOLDUNDERREPURCHASE.LEVEL.NONE.Q.1@CPP',
    'securities sold short':
        'CPP.SECURITIESSOLDSHORT.LEVEL.NONE.Q.1@CPP',
    'derivative liabilities':
        'CPP.DERIVATIVELIABILITIES.LEVEL.NONE.Q.1@CPP',
    # 'other' in this section → CPP.OTHERINVESTMENTLIABILITIES (resolved by context)
    'total investment liabilities':
        'CPP.TOTALINVESTMENTLIABILITIES.LEVEL.NONE.Q.1@CPP',

    # ── Below the liabilities block ───────────────────────────────────────────
    'cash and cash equivalents':
        'CPP.CASH.LEVEL.NONE.Q.1@CPP',
    'pending trades receivable':
        'CPP.PENDINGTRADESRECEIVABLE.LEVEL.NONE.Q.1@CPP',
    'pending trades payable':
        'CPP.PENDINGTRADESPAYABLE.LEVEL.NONE.Q.1@CPP',
    'net investments':
        'CPP.NETINVESTMENTS.LEVEL.NONE.Q.1@CPP',
}

# 'Other' appears twice: once in the Investment receivables section and once in
# the Investment liabilities section.  The extractor resolves which code applies
# by tracking the current section header.
OTHER_ROW_CODES = {
    'investment_receivables': 'CPP.OTHERINVESTMENTRECEIVABLES.LEVEL.NONE.Q.1@CPP',
    'investment_liabilities': 'CPP.OTHERINVESTMENTLIABILITIES.LEVEL.NONE.Q.1@CPP',
}

# ── PDF identification keyword ─────────────────────────────────────────────────
# Both keywords must match (whitespace-collapsed, lowercase) to confirm the
# correct page within the quarterly financial statements PDF.
TABLE_KEYWORDS = [
    'condensed interim consolidated schedule of investment portfolio',
    'public equities',
]

# ── Extraction quality thresholds ────────────────────────────────────────────
# Warn (don't raise) when a quarter has fewer populated codes than this.
# 22 is the practical minimum for FY2025+ PDFs (CPP.INVESTFUNDS is blank for
# older FY2022-FY2024 quarters by design, so 20 is the floor for those).
MIN_EXPECTED_CODES = 20

# Raise RuntimeError if the CPP website yields fewer quarterly links than this.
# 3 = at least the current fiscal year's Q1/Q2/Q3 must be present.
MIN_EXPECTED_LINKS = 3

# ── META file template ────────────────────────────────────────────────────────
META_TEMPLATE = {
    'FREQUENCY':            'Q',
    'MULTIPLIER':           6,            # values in CAD millions (10^6)
    'AGGREGATION_TYPE':     'END_OF_PERIOD',
    'UNIT_TYPE':            'STOCK',
    'DATA_TYPE':            'CURRENCY',
    'DATA_UNIT':            'CAD',
    'SEASONALLY_ADJUSTED':  'NSA',
    'ANNUALIZED':           False,
    'STATE':                'ACTIVE',
    'PROVIDER_MEASURE_URL': BASE_URL,
    'PROVIDER':             'AfricaAI',
    'SOURCE':               'CPP',
    'SOURCE_DESCRIPTION':   'CPP Investments',
    'COUNTRY':              'CAN',
    'DATASET':              'CANF_CPP',
}
