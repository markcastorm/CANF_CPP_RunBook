"""
One-shot: re-extract all PDFs found under the given run dirs and rebuild
the master CSV + outputs from scratch.  No scraping / downloads required.

Usage:
    python rebuild_master.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)-14s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# Put project root on path so imports resolve correctly
sys.path.insert(0, str(Path(__file__).parent))

import config
import extractor
import file_generator

logger = logging.getLogger('rebuild_master')


def _find_pdfs(root: Path) -> list[str]:
    """Walk root and return all .pdf paths."""
    pdfs = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith('.pdf'):
                pdfs.append(str(Path(dirpath) / fn))
    return pdfs


def main() -> None:
    # Wipe master CSV so we start completely fresh
    master = Path(config.MASTER_CSV)
    if master.exists():
        master.unlink()
        logger.info('Removed stale master CSV: %s', master)

    # Collect all PDFs from the first full run (all historical quarters on disk)
    run_root = Path(__file__).parent / 'output' / '20261112_100731'
    if not run_root.exists():
        logger.error('Historical PDF cache not found: %s', run_root)
        sys.exit(1)

    pdf_paths = _find_pdfs(run_root)
    logger.info('Found %d PDFs under %s', len(pdf_paths), run_root)

    # Also add the latest 2 PDFs from the subsequent run (not strictly needed
    # since the first run already has them, but harmless to include)
    run_root2 = Path(__file__).parent / 'output' / '20261112_101416'
    extra = _find_pdfs(run_root2) if run_root2.exists() else []
    if extra:
        logger.info('Also including %d PDFs from %s', len(extra), run_root2)
        pdf_paths.extend(extra)

    # Deduplicate by filename to avoid re-extracting the same PDF twice
    seen: set[str] = set()
    unique_pdfs: list[str] = []
    for p in pdf_paths:
        name = Path(p).name
        if name not in seen:
            seen.add(name)
            unique_pdfs.append(p)
    logger.info('Unique PDFs after dedup: %d', len(unique_pdfs))

    # Extract
    results = extractor.extract_all(unique_pdfs, max_workers=config.BACKFILL_MAX_WORKERS)

    # Collect rows; track the most recent PDF for the LATEST output
    new_rows: dict[str, dict[str, int]] = {}
    pdf_latest: list[tuple[str, list[str]]] = []

    for pdf_path, result in results:
        if result is None:
            logger.warning('Extraction failed: %s', pdf_path)
            continue
        columns = result['columns']
        data    = result['data']
        primary = columns[0] if columns else None
        if primary:
            pdf_latest.append((primary, [q for q in columns if q]))
        for col_idx, qid in enumerate(columns):
            if qid is None:
                continue
            if qid not in new_rows:
                new_rows[qid] = {}
            for code, vals in data.items():
                if col_idx < len(vals) and vals[col_idx] is not None:
                    new_rows[qid][code] = vals[col_idx]

    latest_quarters = max(pdf_latest, key=lambda t: t[0])[1] if pdf_latest else None
    logger.info('Quarters extracted: %d  →  %s', len(new_rows), sorted(new_rows))
    logger.info('Latest PDF quarters: %s', latest_quarters)

    # Write master CSV (2 header rows + data) and generate outputs
    file_generator.update_master_csv(new_rows)
    run_dir = file_generator.generate_outputs(latest_quarters=latest_quarters)
    logger.info('Done. Outputs in: %s', run_dir)


if __name__ == '__main__':
    main()
