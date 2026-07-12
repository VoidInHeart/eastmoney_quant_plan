from __future__ import annotations

import argparse
import logging

from eastmoney_quant import config
from eastmoney_quant.prediction import load_factor_data, run_prediction, track_logged_predictions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and track the latest strategy portfolio")
    parser.add_argument("--top-n", type=int, default=config.TOP_N, help="number of stocks in the suggested portfolio")
    parser.add_argument("--refresh-factors", action="store_true", help="rebuild factor data from clean_daily.parquet first")
    parser.add_argument("--no-log", action="store_true", help="do not append this run to prediction_log.csv")
    parser.add_argument("--track-only", action="store_true", help="only update prediction_tracking.csv from existing logs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    if args.track_only:
        factor_data = load_factor_data(refresh=args.refresh_factors)
        tracking = track_logged_predictions(factor_data)
        tracking.to_csv(config.PREDICTION_TRACKING_PATH, index=False, encoding="utf-8-sig")
        print(f"Saved tracking file: {config.PREDICTION_TRACKING_PATH}")
        if tracking.empty:
            print("No completed prediction period can be tracked yet.")
        else:
            display_cols = ["signal_date", "as_of_date", "holding_days", "portfolio_return", "covered_holdings"]
            print(tracking[display_cols].tail(10).to_string(index=False))
        return

    result = run_prediction(top_n=args.top_n, refresh_factors=args.refresh_factors, append_log=not args.no_log)

    print(f"Signal date: {result.summary['signal_date']}")
    print(f"Saved latest portfolio: {config.LATEST_PORTFOLIO_PATH}")
    print(f"Saved tracking file: {config.PREDICTION_TRACKING_PATH}")
    print()
    print(result.portfolio[["rank", "code", "name", "score", "weight", "close"]].to_string(index=False))
    if not result.tracking.empty:
        print()
        print("Tracked prediction returns:")
        display_cols = ["signal_date", "as_of_date", "holding_days", "portfolio_return", "covered_holdings"]
        print(result.tracking[display_cols].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
