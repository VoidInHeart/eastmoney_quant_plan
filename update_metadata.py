from __future__ import annotations

import argparse

import pandas as pd

from eastmoney_quant import config
from eastmoney_quant.metadata import (
    fetch_delisting_registry,
    fetch_industry_changes,
    fetch_share_changes,
    fetch_suspensions,
    fetch_suspensions_incremental,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch point-in-time A-share metadata through AKShare")
    parser.add_argument("--delisting", action="store_true", help="refresh SSE/SZSE delisting registries")
    parser.add_argument("--suspension-dates", nargs="*", default=[], help="specific dates, e.g. 20260601 20260622")
    parser.add_argument("--suspension-from-clean", action="store_true", help="incrementally cache suspension data for dates in clean_daily")
    parser.add_argument("--suspension-start", default=None, help="lower date bound for --suspension-from-clean")
    parser.add_argument("--suspension-end", default=None, help="upper date bound for --suspension-from-clean")
    parser.add_argument("--suspension-limit", type=int, default=20, help="maximum new dates per incremental run; use 0 for no limit")
    parser.add_argument("--suspension-commit-every", type=int, default=20, help="persist every N incremental suspension dates")
    parser.add_argument("--retry-failed", action="store_true", help="retry failed incremental suspension dates")
    parser.add_argument("--industry-codes", nargs="*", default=[], help="codes for CNInfo industry-change history")
    parser.add_argument("--share-codes", nargs="*", default=[], help="codes for CNInfo share-change history")
    parser.add_argument("--start-date", default="20200101", help="start date for industry/share events")
    parser.add_argument("--end-date", default="20260712", help="end date for industry/share events")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="delay between per-stock requests")
    parser.add_argument("--dry-run", action="store_true", help="show requested enrichment without calling external APIs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    requested = {
        "delisting": args.delisting,
        "suspension_dates": args.suspension_dates,
        "suspension_from_clean": args.suspension_from_clean,
        "industry_codes": args.industry_codes,
        "share_codes": args.share_codes,
    }
    if not any([args.delisting, args.suspension_dates, args.suspension_from_clean, args.industry_codes, args.share_codes]):
        raise SystemExit("select at least one metadata action")
    if args.dry_run:
        print(requested)
        return
    if args.delisting:
        print(f"delisting rows: {len(fetch_delisting_registry())}")
    if args.suspension_dates:
        print(f"suspension rows: {len(fetch_suspensions(args.suspension_dates, args.sleep_seconds))}")
    if args.suspension_from_clean:
        if not config.CLEAN_DAILY_PATH.exists():
            raise SystemExit("clean_daily.parquet is required for --suspension-from-clean")
        dates = pd.to_datetime(pd.read_parquet(config.CLEAN_DAILY_PATH, columns=["date"])["date"]).dropna().drop_duplicates()
        if args.suspension_start:
            dates = dates[dates >= pd.Timestamp(args.suspension_start)]
        if args.suspension_end:
            dates = dates[dates <= pd.Timestamp(args.suspension_end)]
        dates = sorted(dates)
        if args.suspension_limit > 0:
            dates = dates[: args.suspension_limit]
        log = fetch_suspensions_incremental(
            dates,
            args.sleep_seconds,
            retry_failed=args.retry_failed,
            commit_every=args.suspension_commit_every,
        )
        print(f"suspension fetch log: success={(log['status'] == 'success').sum()}, failed={(log['status'] == 'failed').sum()}")
    if args.industry_codes:
        print(f"industry change rows: {len(fetch_industry_changes(args.industry_codes, args.start_date, args.end_date, args.sleep_seconds))}")
    if args.share_codes:
        print(f"share change rows: {len(fetch_share_changes(args.share_codes, args.start_date, args.end_date, args.sleep_seconds))}")


if __name__ == "__main__":
    main()
