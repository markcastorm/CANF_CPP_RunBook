"""
Scraper for CPP Investments quarterly financial statements.

Key facts discovered via live site exploration:
  - ALL fiscal-year sections (FY1999–FY2027) are present in the initial page HTML.
    The "Load more" button only toggles CSS visibility; it does not load new data.
    We therefore skip clicking Load more and simply parse the initial page source.
  - FY2006 and a few others use relative URLs (/newsroom/...) — resolved to absolute.
  - PDF CDN URLs are Cloudflare-protected: requests without browser cookies return
    a 202-HTML challenge page.  We download using the Selenium session cookies as the
    primary path, with a browser-triggered download as a fallback.

Navigation flow:
  1. Load BASE_URL with a stealth Selenium driver.
  2. Extract all cookies from the driver (for CDN auth).
  3. Parse page_source with BeautifulSoup — no Load more clicks needed.
  4. Collect every "Nth quarter fiscal YYYY" PDF link for CPP FY >= START_FISCAL_YEAR.
     Highlights / Results-Overview PDFs are excluded.
  5. Skip quarters already in state.json (or existing_quarters set for backfill mode).
  6. Download PDFs via requests + session cookies.
     If the response is HTML (Cloudflare challenge), fall back to driver.get(url) with
     CDP-managed download directory and a file-watcher.

Returns list of dicts per successfully downloaded PDF:
    {
        'quarter_id':     '2026-Q3',           # calendar quarter YYYY-QN
        'comparative_id': '2026-Q1',           # March 31 year-end (always Q1)
        'cpp_quarter':    2,                   # 1, 2, or 3
        'cpp_fy':         2027,
        'link_text':      '2nd quarter fiscal 2027',
        'pdf_url':        'https://cdn3.cppinvestments.com/...',
        'pdf_path':       'D:/...downloads/.../filename.pdf',
        'run_dir':        'D:/...downloads/20261111_103000/',
    }
"""

import os
import re
import sys
import json
import time
import random
import logging
import subprocess
from datetime import datetime

import requests
import urllib3

import config

logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BASE_SITE = 'https://www.cppinvestments.com'

_DL_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'application/pdf,application/octet-stream,*/*;q=0.8',
    'Referer': config.BASE_URL,
}

_ORDINAL_TO_NUM = {'1st': 1, '2nd': 2, '3rd': 3}


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _human_delay(lo=0.8, hi=2.2):
    time.sleep(random.uniform(lo, hi))


def get_chrome_version():
    """Detect installed Chrome major version.  Windows registry first, then CLI."""
    if sys.platform == 'win32':
        for hive_flag in (False, True):
            try:
                import winreg
                hive = winreg.HKEY_LOCAL_MACHINE if hive_flag else winreg.HKEY_CURRENT_USER
                key  = winreg.OpenKey(hive, r'SOFTWARE\Google\Chrome\BLBeacon')
                ver  = winreg.QueryValueEx(key, 'version')[0].split('.')[0]
                return int(ver)
            except Exception:
                pass

    for cmd in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']:
        try:
            out = subprocess.check_output(
                [cmd, '--version'], stderr=subprocess.DEVNULL
            ).decode()
            return int(out.strip().split()[-1].split('.')[0])
        except Exception:
            continue
    return None


def _build_driver(download_dir):
    """Create an undetected Chrome driver with stealth and CDP download support."""
    import undetected_chromedriver as uc
    from selenium_stealth import stealth

    download_dir = str(download_dir)   # Path objects are not JSON-serialisable by Chrome

    opts = uc.ChromeOptions()
    if config.HEADLESS_MODE:
        opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1920,1080')
    opts.add_argument('--lang=en-US,en;q=0.9')
    opts.add_experimental_option('prefs', {
        'download.default_directory':         download_dir,
        'download.prompt_for_download':       False,
        'download.directory_upgrade':         True,
        'plugins.always_open_pdf_externally': True,
        'safebrowsing.enabled':               True,
    })

    version = get_chrome_version()
    kwargs  = {'options': opts, 'use_subprocess': True}
    if version:
        kwargs['version_main'] = version

    driver = uc.Chrome(**kwargs)

    stealth(
        driver,
        languages=['en-US', 'en'],
        vendor='Google Inc.',
        platform='Win32',
        webgl_vendor='Intel Inc.',
        renderer='Intel Iris OpenGL Engine',
        fix_hairline=True,
    )

    driver.execute_cdp_cmd(
        'Page.setDownloadBehavior',
        {'behavior': 'allow', 'downloadPath': download_dir},
    )
    return driver


# ── URL helpers ───────────────────────────────────────────────────────────────

def _absolute_url(href):
    """Resolve relative PDF hrefs (e.g. /newsroom/...) to absolute URLs."""
    if href.startswith('http'):
        return href
    if href.startswith('/'):
        return _BASE_SITE + href
    return href


def _pdf_filename_from_url(url):
    """Extract filename from a CDN PDF URL."""
    path = url.split('?')[0].rstrip('/')
    for part in reversed(path.split('/')):
        if part.lower().endswith('.pdf'):
            return part
    return path.split('/')[-1] + '.pdf'


# ── Quarter ID helpers ────────────────────────────────────────────────────────

def _parse_quarter_link(link_text):
    """
    Return (cpp_q_num, cpp_fy) for a quarterly FS link, or (None, None) if not one.

    Accepts: "1st quarter fiscal 2027", "2nd quarter fiscal 2026", etc.
    Rejects: anything containing 'results', 'highlight', 'overview'.
    """
    text = link_text.strip().lower()
    if any(kw in text for kw in ('results', 'highlight', 'overview')):
        return None, None
    m = re.match(r'^(1st|2nd|3rd)\s+quarter\s+fiscal\s+(\d{4})$', text)
    if not m:
        return None, None
    return _ORDINAL_TO_NUM[m.group(1)], int(m.group(2))


def _quarter_ids(cpp_q_num, cpp_fy):
    """
    Map a CPP (quarter, fiscal_year) to calendar quarter IDs.

    Returns (current_id, comparative_id) where:
      current     = period-end date as calendar quarter (e.g. '2026-Q3')
      comparative = March 31 year-end              (always 'YYYY-Q1')

    CPP fiscal year starts April 1 of (cpp_fy - 1):
      Q1 → Apr–Jun (cpp_fy-1) → ends Jun 30  → calendar (cpp_fy-1)-Q2
      Q2 → Jul–Sep (cpp_fy-1) → ends Sep 30  → calendar (cpp_fy-1)-Q3
      Q3 → Oct–Dec (cpp_fy-1) → ends Dec 31  → calendar (cpp_fy-1)-Q4
      comparative = Mar 31 (cpp_fy-1)         → calendar (cpp_fy-1)-Q1
    """
    cal_year    = cpp_fy - 1
    cal_q       = config.CPP_QUARTER_TO_CALENDAR_Q[cpp_q_num]
    current     = f'{cal_year}-{cal_q}'
    comparative = f'{cal_year}-Q1'
    return current, comparative


# ── Page parsing ──────────────────────────────────────────────────────────────

def _make_link_item(link_text, href, q_num, fy):
    """Build a standard link dict from validated components."""
    current_q, comparative_q = _quarter_ids(q_num, fy)
    return {
        'link_text':      link_text,
        'pdf_url':        href,
        'cpp_quarter':    q_num,
        'cpp_fy':         fy,
        'quarter_id':     current_q,
        'comparative_id': comparative_q,
    }


def _links_structural(soup):
    """
    Strategy 1 — structural CSS parsing (current primary approach).

    Relies on:
      <div class="fiscal-wrap" id="fiscal-YYYY">
        <h3>...Quarterly reports...</h3>
        <div class="moreInfo">
          <a href="...pdf">1st quarter fiscal 2027</a>
    """
    results = []
    for wrap in soup.find_all('div', class_='fiscal-wrap'):
        div_id = wrap.get('id', '')
        m = re.match(r'^fiscal-(\d{4})$', div_id)
        if not m:
            continue
        if int(m.group(1)) < config.START_FISCAL_YEAR:
            continue
        for h3 in wrap.find_all('h3'):
            if 'quarterly reports' not in h3.get_text(strip=True).lower():
                continue
            more_info = h3.find_next_sibling('div', class_='moreInfo')
            if not more_info:
                continue
            for a in more_info.find_all('a', href=True):
                link_text = a.get_text(strip=True)
                href      = _absolute_url(a['href'])
                if not href.lower().endswith('.pdf'):
                    continue
                q_num, fy = _parse_quarter_link(link_text)
                if q_num is None or fy < config.START_FISCAL_YEAR:
                    continue
                results.append(_make_link_item(link_text, href, q_num, fy))
    results.sort(key=lambda x: (x['cpp_fy'], x['cpp_quarter']))
    return results


def _links_text_search(soup):
    """
    Strategy 2 — text-based search across ALL anchor tags.

    Does NOT rely on any container class or ID — only on the link text
    matching the quarterly pattern.  Survives any CSS/class rename on the site.
    Uses a relaxed regex to tolerate capitalisation variants.
    """
    _RELAXED_Q_RE = re.compile(
        r'^(1st|2nd|3rd)\s+quarter\s+fiscal\s+(\d{4})$',
        re.IGNORECASE,
    )
    results = []
    seen: set[tuple[int, int]] = set()
    for a in soup.find_all('a', href=True):
        link_text = a.get_text(strip=True)
        href      = _absolute_url(a['href'])
        if not href.lower().endswith('.pdf'):
            continue
        if any(kw in link_text.lower() for kw in ('results', 'highlight', 'overview')):
            continue
        m = _RELAXED_Q_RE.match(link_text.strip())
        if not m:
            continue
        q_num = _ORDINAL_TO_NUM.get(m.group(1).lower())
        fy    = int(m.group(2))
        if q_num is None or fy < config.START_FISCAL_YEAR:
            continue
        key = (q_num, fy)
        if key in seen:
            continue
        seen.add(key)
        results.append(_make_link_item(link_text, href, q_num, fy))
    results.sort(key=lambda x: (x['cpp_fy'], x['cpp_quarter']))
    return results


def _parse_quarter_from_url(url: str):
    """
    Try to extract (q_num, fy) from a CPP PDF URL filename.

    Handles patterns like 'Q2-F27-...pdf', 'Q1-F2027-...pdf', 'Q3-F26-...pdf'.
    Returns (None, None) if no match.
    """
    filename = url.split('/')[-1].split('?')[0]
    m = re.search(r'[Qq]([123])[^0-9]*[Ff](20\d{2}|\d{2})\b', filename)
    if m:
        q_num  = int(m.group(1))
        fy_raw = m.group(2)
        fy     = int(fy_raw) if len(fy_raw) == 4 else 2000 + int(fy_raw)
        return q_num, fy
    return None, None


def _links_broad_pdf_scan(soup):
    """
    Strategy 3 — broadest fallback: scan every PDF link on the page.

    Accepts any <a href="*.pdf"> whose anchor text OR URL filename encodes a
    quarterly pattern.  Uses a relaxed regex that accepts 'first'/'second'/'third'
    synonyms and mixed capitalisation.  Also tries to parse quarter info directly
    from the URL filename (e.g. 'Q2-F27-Financial-Statements.pdf').

    This strategy survives even if CPP completely rewrites link text formatting,
    as long as the PDF filename convention stays recognisable.
    """
    _BROAD_Q_RE = re.compile(
        r'\b(1st|2nd|3rd|first|second|third)\b.*?\bquarter\b.*?\bfiscal\b.*?\b(20\d{2})\b',
        re.IGNORECASE,
    )
    _ORDINAL_BROAD = {
        '1st': 1, 'first': 1,
        '2nd': 2, 'second': 2,
        '3rd': 3, 'third': 3,
    }
    results = []
    seen: set[tuple[int, int]] = set()

    for a in soup.find_all('a', href=True):
        href = _absolute_url(a['href'])
        if not href.lower().endswith('.pdf'):
            continue
        if any(kw in href.lower() for kw in ('highlight', 'overview', 'annual')):
            continue

        link_text = a.get_text(strip=True)

        # First try parsing from anchor text
        q_num, fy = None, None
        m = _BROAD_Q_RE.search(link_text)
        if m:
            q_num = _ORDINAL_BROAD.get(m.group(1).lower())
            fy    = int(m.group(2))

        # Fallback: try parsing from URL filename
        if q_num is None:
            q_num, fy = _parse_quarter_from_url(href)

        if q_num is None or fy is None or fy < config.START_FISCAL_YEAR:
            continue
        if any(kw in link_text.lower() for kw in ('results', 'highlight', 'overview')):
            continue

        key = (q_num, fy)
        if key in seen:
            continue
        seen.add(key)

        display_text = link_text or f'{q_num}th quarter fiscal {fy}'
        results.append(_make_link_item(display_text, href, q_num, fy))

    results.sort(key=lambda x: (x['cpp_fy'], x['cpp_quarter']))
    return results


def _collect_quarterly_links(page_source):
    """
    Multi-strategy parser — returns all quarterly FS PDF items for
    FY >= START_FISCAL_YEAR, sorted oldest quarter first.

    Three strategies are tried in order of specificity:
      1. Structural  — relies on CSS classes/IDs (fast, specific, may break on site redesign)
      2. Text-search — scans all <a> tags by anchor text pattern (CSS-agnostic)
      3. Broad scan  — scans all PDF links; also inspects URL filenames (most resilient)

    Raises RuntimeError if all strategies return 0 links.  Warns if the total is
    below config.MIN_EXPECTED_LINKS (site may be partially unavailable).
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_source, 'html.parser')

    links = _links_structural(soup)
    if links:
        logger.info('HTML strategy 1 (structural): %d quarterly links found', len(links))
    else:
        logger.warning(
            'HTML strategy 1 (structural) returned 0 links — '
            'CPP site HTML may have changed; trying text-search fallback'
        )
        links = _links_text_search(soup)
        if links:
            logger.warning(
                'HTML strategy 2 (text-search): %d links — '
                'structural selectors no longer match, update _links_structural()',
                len(links),
            )
        else:
            logger.warning(
                'HTML strategy 2 (text-search) also returned 0 links — '
                'trying broad PDF scan'
            )
            links = _links_broad_pdf_scan(soup)
            if links:
                logger.warning(
                    'HTML strategy 3 (broad-pdf-scan): %d links — '
                    'site HTML has changed significantly; consider updating scraper',
                    len(links),
                )
            else:
                raise RuntimeError(
                    'All HTML parsing strategies returned 0 quarterly links.\n'
                    '  Strategies tried: structural-CSS, text-search, broad-pdf-scan\n'
                    f'  Source: {config.BASE_URL}\n'
                    '  The CPP website HTML structure has changed in an unrecognised way.\n'
                    '  Inspect the page source and update scraper._links_structural().'
                )

    if len(links) < config.MIN_EXPECTED_LINKS:
        logger.warning(
            'Only %d quarterly links found — expected at least %d. '
            'Some fiscal years may be missing or the site is partially unavailable.',
            len(links), config.MIN_EXPECTED_LINKS,
        )

    logger.info(
        'Collected %d quarterly FS PDF links (FY%d+)',
        len(links), config.START_FISCAL_YEAR,
    )
    return links


# ── PDF download ──────────────────────────────────────────────────────────────

def _download_via_requests(url, dest_path, cookies=None):
    """
    Download a PDF using requests + optional CDN session cookies.
    Returns True on success.  Returns False (without raising) on any failure,
    including if the response is an HTML challenge page.
    """
    headers = dict(_DL_HEADERS)
    try:
        resp = requests.get(
            url,
            headers=headers,
            cookies=cookies or {},
            stream=True,
            timeout=120,
            verify=False,
        )
        resp.raise_for_status()

        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            logger.debug(f'requests: got HTML (CDN challenge) for {url}')
            return False

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)

        size = os.path.getsize(dest_path)
        if size < 10_000:
            logger.warning(f'requests: suspiciously small file ({size} bytes) → {dest_path}')
            os.remove(dest_path)
            return False

        logger.info(f'requests: {size:,} bytes → {dest_path}')
        return True
    except Exception as exc:
        logger.debug(f'requests download error for {url}: {exc}')
        return False


def _wait_for_download(download_dir, timeout=120):
    """
    Wait until a fully-written .pdf file appears in download_dir.
    Returns the absolute path of the downloaded file, or None on timeout.
    Ignores .crdownload (Chrome partial downloads).
    """
    start = time.time()
    while time.time() - start < timeout:
        pdfs = [
            f for f in os.listdir(download_dir)
            if f.lower().endswith('.pdf') and not f.endswith('.crdownload')
        ]
        if pdfs:
            pdf_path = os.path.join(download_dir, sorted(pdfs)[-1])
            # Confirm file size stabilised (not still being written)
            try:
                size1 = os.path.getsize(pdf_path)
                time.sleep(0.5)
                size2 = os.path.getsize(pdf_path)
                if size1 == size2 and size1 > 10_000:
                    logger.info(f'browser download complete: {size1:,} bytes → {pdf_path}')
                    return pdf_path
            except OSError:
                pass
        time.sleep(0.5)
    logger.warning(f'Download timed out after {timeout}s in {download_dir}')
    return None


def _download_via_browser(driver, url, dest_dir):
    """
    Navigate the driver to url and wait for the PDF to land in dest_dir.
    Uses the CDP download behaviour already set on the driver.
    Returns the downloaded file path, or None on failure.
    """
    os.makedirs(dest_dir, exist_ok=True)
    # Update the CDP download path for this specific quarter's directory
    driver.execute_cdp_cmd(
        'Page.setDownloadBehavior',
        {'behavior': 'allow', 'downloadPath': dest_dir},
    )
    # Clear any existing PDFs so _wait_for_download picks up the new file
    for f in os.listdir(dest_dir):
        if f.lower().endswith('.pdf'):
            try:
                os.remove(os.path.join(dest_dir, f))
            except OSError:
                pass

    logger.info(f'browser download: navigating to {url}')
    driver.get(url)
    _human_delay(1.0, 2.0)

    return _wait_for_download(dest_dir, timeout=300)


def _download_pdf(url, dest_path, driver=None, cdn_cookies=None):
    """
    Download a PDF.  Tries requests first; falls back to the Selenium driver.
    Returns the final file path on success, or None on failure.
    """
    # Try fast path: requests with CDN session cookies
    if _download_via_requests(url, dest_path, cookies=cdn_cookies):
        return dest_path

    # Fallback: use the Selenium driver
    if driver is not None:
        dest_dir  = os.path.dirname(dest_path)
        result    = _download_via_browser(driver, url, dest_dir)
        if result:
            # Rename to expected filename if Chrome chose a different name
            expected = dest_path
            if result != expected and os.path.basename(result) != os.path.basename(expected):
                try:
                    os.rename(result, expected)
                    return expected
                except OSError:
                    return result
            return result

    logger.error(f'All download strategies failed for {url}')
    return None


# ── State management ──────────────────────────────────────────────────────────

def _load_state():
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _save_state(state):
    state_dir = os.path.dirname(config.STATE_FILE)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(config.STATE_FILE, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2)


def mark_quarter_processed(quarter_id, pdf_path, cpp_fy, cpp_quarter):
    """Record a successfully processed quarter in state.json."""
    state = _load_state()
    state.setdefault('processed_quarters', {})[quarter_id] = {
        'processed_at': datetime.now().isoformat(timespec='seconds'),
        'pdf_path':     pdf_path,
        'cpp_fy':       cpp_fy,
        'cpp_quarter':  cpp_quarter,
    }
    _save_state(state)


def _quarter_already_processed(quarter_id):
    return quarter_id in _load_state().get('processed_quarters', {})


# ── Master CSV helper ─────────────────────────────────────────────────────────

def load_existing_quarters():
    """
    Return a set of calendar quarter IDs (e.g. '2026-Q3') that already have
    at least one data value in the master CSV.
    """
    if not os.path.exists(config.MASTER_CSV):
        return set()

    import csv
    quarters = set()
    with open(config.MASTER_CSV, encoding='utf-8', newline='') as fh:
        for i, row in enumerate(csv.reader(fh)):
            if i < 2:   # skip codes row (row 1) and descriptions row (row 2)
                continue
            if not row:
                continue
            date_cell = row[0].strip()
            if re.match(r'^\d{4}-Q[1-4]$', date_cell):
                if any(v.strip() for v in row[1:]):
                    quarters.add(date_cell)
    logger.info(f'Master CSV existing quarters: {sorted(quarters)}')
    return quarters


# ── Core scrape session ───────────────────────────────────────────────────────

def _run_scrape_session(to_download, run_dir):
    """
    Open one Selenium session, scrape the page once to confirm all links,
    then download each PDF in to_download.

    to_download: list of dicts from _collect_quarterly_links already filtered
                 to only the quarters we need.
    run_dir:     base directory for this run's downloads.

    Returns list of successfully downloaded dicts (pdf_path added).
    """
    run_dir = str(run_dir)   # normalise Path → str for os.path.join and CDP
    driver = None
    results = []
    try:
        driver = _build_driver(run_dir)

        logger.info(f'Loading {config.BASE_URL}')
        driver.get(config.BASE_URL)
        _human_delay(3.0, 5.0)

        # Extract CDN session cookies (used for requests-based downloads)
        cdn_cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        logger.info(f'Captured {len(cdn_cookies)} CDN session cookies')

        for item in to_download:
            qid      = item['quarter_id']
            url      = item['pdf_url']
            filename = _pdf_filename_from_url(url)
            dest_dir = os.path.join(run_dir, qid.replace('-', '_'))
            dest     = os.path.join(dest_dir, filename)

            logger.info(f'[{qid}] Downloading: {item["link_text"]}  ({filename})')
            path = _download_pdf(url, dest, driver=driver, cdn_cookies=cdn_cookies)

            if path:
                item = dict(item)
                item['pdf_path'] = path
                item['run_dir']  = run_dir
                results.append(item)
            else:
                logger.error(f'[{qid}] Download failed: {url}')

            _human_delay(0.5, 1.5)   # polite gap between downloads

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return results


# ── Public entry points ───────────────────────────────────────────────────────

def download():
    """
    Normal run mode.

    Fetches page once to discover available quarterly FS PDFs (FY >= START_FISCAL_YEAR),
    skips already-processed quarters, and downloads only the new ones.

    Returns list of download result dicts.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir   = os.path.join(config.DOWNLOAD_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # Collect links using a quick headless page fetch
    all_links = _fetch_all_links(run_dir)

    if not all_links:
        logger.warning('No quarterly links found on page')
        return []

    # Filter: skip already-processed quarters (from state.json)
    to_download = []
    for item in all_links:
        qid = item['quarter_id']
        if config.SKIP_IF_PROCESSED and _quarter_already_processed(qid):
            entry = _load_state().get('processed_quarters', {}).get(qid, {})
            ts    = entry.get('processed_at', '?') if isinstance(entry, dict) else str(entry)
            logger.info(f'SKIP {qid} — already processed on {ts}')
        else:
            to_download.append(item)

    if not to_download:
        logger.info('All quarters already processed — nothing to download')
        return []

    logger.info(f'{len(to_download)} quarter(s) to download: {[x["quarter_id"] for x in to_download]}')
    results = _run_scrape_session(to_download, run_dir)
    logger.info(f'Scraper complete: {len(results)}/{len(to_download)} PDFs downloaded')
    return results


def discover_and_download(existing_quarters=None, run_dir=None):
    """
    Backfill mode.

    Discovers all available quarterly FS PDFs from FY >= START_FISCAL_YEAR,
    compares against existing_quarters, downloads only the gaps.

    Args:
        existing_quarters: set/list of calendar quarter IDs already in master CSV.
                           Defaults to reading master CSV.
        run_dir:           download base directory (defaults to timestamped subdir).

    Returns list of download result dicts for newly downloaded quarters.
    """
    if existing_quarters is None:
        existing_quarters = load_existing_quarters()
    existing = set(existing_quarters)

    if run_dir is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        run_dir   = os.path.join(config.DOWNLOAD_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    all_links = _fetch_all_links(run_dir)
    if not all_links:
        logger.warning('No quarterly links found on page')
        return []

    missing = [item for item in all_links if item['quarter_id'] not in existing]

    if not missing:
        logger.info('No missing quarters — master CSV is up to date')
        return []

    logger.info(
        f'Missing quarters ({len(missing)}): {[x["quarter_id"] for x in missing]}'
    )
    results = _run_scrape_session(missing, run_dir)
    logger.info(f'Backfill complete: {len(results)}/{len(missing)} PDFs downloaded')
    return results


# ── Shared page-fetch helper ──────────────────────────────────────────────────

def _fetch_all_links(run_dir):
    """
    Open one Selenium session, load the financial results page, and return all
    quarterly FS PDF items for FY >= START_FISCAL_YEAR.

    All fiscal-year divs are present in the initial page HTML (Load more only
    changes CSS visibility), so one page load is sufficient.
    """
    driver = None
    try:
        driver = _build_driver(run_dir)
        logger.info(f'Loading {config.BASE_URL} to collect links')
        driver.get(config.BASE_URL)
        _human_delay(3.0, 5.0)

        all_links = _collect_quarterly_links(driver.page_source)
        return all_links
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
