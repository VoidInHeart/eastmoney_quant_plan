"""Slow, resumable CNInfo industry/share-history backfill through AKShare."""

from __future__ import annotations

import argparse
import json

import pandas as pd

from eastmoney_quant import config
from eastmoney_quant.metadata import fetch_cninfo_events_incremental, pending_cninfo_codes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally backfill CNInfo industry and share-change events")
    parser.add_argument("--kind", choices=["all", "industry", "share"], default="all", help="which event history to fetch")
    parser.add_argument("--batch-size", type=int, default=0, help="new codes per kind; 0 means all remaining codes")
    parser.add_argument("--commit-every", type=int, default=50, help="persist every N requested codes")
    parser.add_argument("--sleep-seconds", type=float, default=0.5, help="delay between remote requests")
    parser.add_argument("--start-date", default=config.START_DATE, help="history start date in YYYYMMDD")
    parser.add_argument("--end-date", default=config.END_DATE, help="history end date in YYYYMMDD")
    parser.add_argument("--codes", nargs="*", default=None, help="optional explicit stock codes")
    parser.set_defaults(retry_failed=True)
    parser.add_argument("--retry-failed", dest="retry_failed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-failed", dest="retry_failed", action="store_false", help="do not retry codes with a previous failed response")
    parser.add_argument("--dry-run", action="store_true", help="show the next batch without calling AKShare")
    return parser.parse_args()


def source_codes(explicit_codes: list[str] | None) -> list[str]:
    if explicit_codes:
        return sorted({str(code).zfill(6) for code in explicit_codes})
    if not config.CLEAN_DAILY_PATH.exists():
        raise SystemExit("clean_daily.parquet is required when --codes is not supplied")
    return sorted(
        pd.read_parquet(config.CLEAN_DAILY_PATH, columns=["code"])["code"].astype(str).str.zfill(6).unique()
    )


def summarize(kind: str, codes: list[str], retry_failed: bool) -> dict[str, object]:
    pending = pending_cninfo_codes(kind, codes, retry_failed=retry_failed)
    return {"kind": kind, "universe_codes": len(codes), "pending_codes": len(pending), "next_codes": pending}


def main() -> None:
    args = parse_args()
    if args.batch_size < 0:
        raise SystemExit("--batch-size must be non-negative; 0 means all remaining codes")
    codes = source_codes(args.codes)
    kinds = ["industry", "share"] if args.kind == "all" else [args.kind]
    statuses: list[dict[str, object]] = []
    for kind in kinds:
        status = summarize(kind, codes, args.retry_failed)
        pending = status.pop("next_codes")
        batch = pending if args.batch_size == 0 else pending[: args.batch_size]
        status["requested_code_count"] = len(batch)
        status["requested_code_preview"] = batch[:10]
        if args.dry_run:
            status["mode"] = "dry_run"
        else:
            log = fetch_cninfo_events_incremental(
                kind,
                batch,
                start_date=args.start_date,
                end_date=args.end_date,
                sleep_seconds=args.sleep_seconds,
                retry_failed=args.retry_failed,
                commit_every=args.commit_every,
            )
            status["mode"] = "fetched"
            status["successful_codes"] = int((log["status"] == "success").sum()) if not log.empty else 0
            status["failed_codes"] = int((log["status"] == "failed").sum()) if not log.empty else 0
        statuses.append(status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
    config.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULT_DIR / "cninfo_backfill_status.json").write_text(
        json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
