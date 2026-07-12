from __future__ import annotations

import argparse
import logging

from eastmoney_quant import config
from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.data_clean import clean_daily_data, save_clean_data
from eastmoney_quant.data_fetch import fetch_all_stock_history, make_sample_data
from eastmoney_quant.factor import build_factors
from eastmoney_quant.report import export_report
from eastmoney_quant.signal import build_rebalance_signal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Eastmoney A-share multi-factor backtest prototype")
    parser.add_argument("--sample", action="store_true", help="run with deterministic offline sample data")
    parser.add_argument("--force-fetch", action="store_true", help="ignore raw data cache and fetch again")
    parser.add_argument("--top-n", type=int, default=config.TOP_N, help="number of stocks held each month")
    parser.add_argument("--limit", type=int, default=None, help="download only the first N stocks for a quick check")
    parser.add_argument("--codes", nargs="*", default=None, help="download only these stock codes, e.g. --codes 000001 600519")
    parser.add_argument(
        "--history-source",
        choices=["auto", "eastmoney", "tencent"],
        default=config.HISTORY_SOURCE,
        help="daily history source; use tencent if Eastmoney keeps disconnecting",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    config.ensure_directories()

    if args.sample:
        raw_data = make_sample_data()
    else:
        raw_data = fetch_all_stock_history(
            start_date=config.START_DATE,
            end_date=config.END_DATE,
            adjust=config.ADJUST,
            codes=args.codes,
            limit=args.limit,
            history_source=args.history_source,
            force=args.force_fetch,
        )

    clean_data = clean_daily_data(raw_data)
    save_clean_data(clean_data)

    factor_data = build_factors(clean_data)
    factor_data.to_parquet(config.FACTOR_DAILY_PATH, index=False)

    signal = build_rebalance_signal(factor_data, top_n=args.top_n)
    signal.to_parquet(config.SIGNAL_PATH, index=False)

    result = run_backtest(factor_data, signal)
    export_report(result)


if __name__ == "__main__":
    main()
