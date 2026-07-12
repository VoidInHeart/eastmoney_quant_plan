from __future__ import annotations

import argparse
import logging

import pandas as pd

from eastmoney_quant import config
from eastmoney_quant.update import infer_update_window, safe_incremental_update


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely update existing raw A-share cache")
    parser.add_argument("--lookback-days", type=int, default=15, help="refetch recent calendar days and merge")
    parser.add_argument("--end-date", default=None, help="end date in YYYYMMDD; default is today")
    parser.add_argument(
        "--history-source",
        choices=["auto", "eastmoney", "tencent"],
        default="tencent",
        help="data source used for incremental history",
    )
    parser.add_argument("--codes", nargs="*", default=None, help="update only these stock codes")
    parser.add_argument("--limit", type=int, default=None, help="update only the first N codes for a quick check")
    parser.add_argument("--no-backup", action="store_true", help="do not copy the old raw cache before replacing")
    parser.add_argument("--no-rebuild", action="store_true", help="only update raw cache; skip factors/backtest/report")
    parser.add_argument("--no-predict", action="store_true", help="do not append a new prediction after rebuild")
    parser.add_argument("--top-n", type=int, default=config.TOP_N, help="portfolio size for rebuild/prediction")
    parser.add_argument("--dry-run", action="store_true", help="show update window and code count without downloading")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    if args.dry_run:
        raw = pd.read_parquet(config.RAW_DAILY_PATH)
        start_date, end_date = infer_update_window(raw, args.lookback_days, args.end_date)
        codes = [str(code).zfill(6) for code in args.codes] if args.codes else sorted(raw["code"].astype(str).str.zfill(6).unique())
        if args.limit is not None:
            codes = codes[: args.limit]
        print(f"Raw cache: {config.RAW_DAILY_PATH}")
        print(f"Update window: {start_date} -> {end_date}")
        print(f"Codes to update: {len(codes)}")
        print("Dry run only; no files changed.")
        return

    result = safe_incremental_update(
        lookback_days=args.lookback_days,
        end_date=args.end_date,
        history_source=args.history_source,
        codes=args.codes,
        limit=args.limit,
        backup=not args.no_backup,
        rebuild=not args.no_rebuild,
        run_predict=not args.no_predict,
        top_n=args.top_n,
    )
    print("Safe update completed.")
    print(f"Raw rows: {result.old_rows} -> {result.merged_rows}")
    print(f"Latest date: {result.old_latest_date.date()} -> {result.merged_latest_date.date()}")
    print(f"Updated codes: {result.updated_codes}; skipped codes: {result.skipped_codes}")
    if result.backup_path:
        print(f"Backup: {result.backup_path}")


if __name__ == "__main__":
    main()
