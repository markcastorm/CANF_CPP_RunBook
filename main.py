"""
CANF_CPP pipeline entry point.

Usage
-----
  python main.py            # normal run (respects config.BACKFILL_ENABLED)
  python main.py --backfill # force full backfill
  python main.py --extract-only <pdf_path> [<pdf_path> ...]
                            # extract specific PDFs without scraping
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('main')


def _extract_only(pdf_paths: list[str]) -> None:
    import config
    import extractor
    import file_generator

    results  = extractor.extract_all(pdf_paths, max_workers=config.BACKFILL_MAX_WORKERS)

    import orchestrator
    new_rows, latest_quarters = orchestrator._extraction_results_to_rows(results)

    if new_rows:
        file_generator.update_master_csv(new_rows)
        run_dir = file_generator.make_run_dir()
        file_generator.generate_outputs(run_dir, latest_quarters=latest_quarters)
        logger.info('Outputs written to %s', run_dir)
    else:
        logger.warning('No data extracted.')


def main() -> None:
    parser = argparse.ArgumentParser(description='CANF_CPP pipeline')
    group  = parser.add_mutually_exclusive_group()
    group.add_argument('--backfill',     action='store_true',
                       help='Force full backfill of all missing quarters')
    group.add_argument('--extract-only', nargs='+', metavar='PDF',
                       help='Extract specific PDFs without scraping')
    args = parser.parse_args()

    if args.extract_only:
        _extract_only(args.extract_only)
    elif args.backfill:
        import orchestrator
        orchestrator.backfill_run()
    else:
        import orchestrator
        orchestrator.run()


if __name__ == '__main__':
    main()
