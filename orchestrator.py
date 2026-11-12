"""
Orchestrates the full CANF_CPP pipeline:
  scraper → extractor (multiprocessing) → master CSV update → output files

Entry points
------------
run()           Normal run: download latest / backfill missing, then process.
backfill_run()  Force-backfill all quarters not in master CSV.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import config
import scraper
import extractor
import file_generator

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extraction_results_to_rows(
    results: list[tuple[str, Optional[dict]]],
) -> tuple[dict[str, dict[str, int]], Optional[list[str]]]:
    """
    Flatten extractor output into {quarter_id: {code: value}} for both columns.
    Skips failed extractions (result is None).

    Also returns the two quarter IDs from the most recently released PDF
    (the one whose primary quarter sorts highest), or None if no PDFs succeeded.
    The list is [primary_qid, comparative_qid] — PDF column order.
    """
    rows: dict[str, dict[str, int]] = {}
    # track (primary_qid, [primary_qid, comparative_qid]) per successful PDF
    pdf_latest: list[tuple[str, list[str]]] = []

    for pdf_path, result in results:
        if result is None:
            logger.warning('Skipping failed extraction: %s', pdf_path)
            continue

        columns = result['columns']   # e.g. ['2026-Q3', '2026-Q1']
        data    = result['data']      # {code: [col0_val, col1_val]}

        primary = columns[0] if columns else None
        if primary:
            pdf_latest.append((primary, [q for q in columns if q]))

        for col_idx, qid in enumerate(columns):
            if qid is None:
                continue
            if qid not in rows:
                rows[qid] = {}
            for code, vals in data.items():
                if col_idx < len(vals) and vals[col_idx] is not None:
                    rows[qid][code] = vals[col_idx]

    latest_quarters: Optional[list[str]] = None
    if pdf_latest:
        # pick the PDF whose primary quarter is the most recent
        latest_quarters = max(pdf_latest, key=lambda t: t[0])[1]

    return rows, latest_quarters


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run() -> None:
    """
    Standard pipeline run.

    Behaviour controlled by config flags:
      SKIP_IF_PROCESSED  — skip if latest quarter already in state.json
      BACKFILL_ENABLED   — scan master CSV for gaps and download missing PDFs
    """
    run_dir = file_generator.make_run_dir()
    logger.info('=== CANF_CPP pipeline start  run_dir=%s ===', run_dir)

    # ── 1. Discover what is already in the master CSV ─────────────────────────
    existing_quarters = scraper.load_existing_quarters()
    logger.info('Existing master CSV quarters: %d', len(existing_quarters))

    # ── 2. Download PDFs ──────────────────────────────────────────────────────
    if config.BACKFILL_ENABLED:
        pdf_infos = scraper.discover_and_download(existing_quarters)
    else:
        pdf_infos = scraper.download()

    if not pdf_infos:
        logger.info('No new PDFs to process — pipeline complete.')
        return

    logger.info('PDFs to extract: %d', len(pdf_infos))

    # ── 3. Extract tables (multiprocessing) ───────────────────────────────────
    pdf_paths = [info['pdf_path'] for info in pdf_infos]
    results   = extractor.extract_all(
        pdf_paths,
        max_workers=config.BACKFILL_MAX_WORKERS,
    )

    # ── 4. Update master CSV ──────────────────────────────────────────────────
    new_rows, latest_quarters = _extraction_results_to_rows(results)
    if new_rows:
        file_generator.update_master_csv(new_rows)
        logger.info('Master CSV updated with %d quarter(s)', len(new_rows))

        # Mark each processed quarter in state.json
        for info in pdf_infos:
            # Only mark if extraction succeeded for that PDF
            pdf_path = info['pdf_path']
            matched  = [r for p, r in results if p == pdf_path and r is not None]
            if matched:
                scraper.mark_quarter_processed(
                    info['quarter_id'],
                    pdf_path,
                    info['cpp_fy'],
                    info['cpp_quarter'],
                )
    else:
        logger.warning('No rows extracted from any PDF.')

    # ── 5. Generate output files ──────────────────────────────────────────────
    if latest_quarters:
        logger.info('Latest PDF quarters: %s', latest_quarters)
    file_generator.generate_outputs(run_dir, latest_quarters=latest_quarters)

    logger.info('=== CANF_CPP pipeline complete  run_dir=%s ===', run_dir)


def backfill_run() -> None:
    """Force a full backfill regardless of state.json."""
    original = config.BACKFILL_ENABLED
    config.BACKFILL_ENABLED = True
    try:
        run()
    finally:
        config.BACKFILL_ENABLED = original
