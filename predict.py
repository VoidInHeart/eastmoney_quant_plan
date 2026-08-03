from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from eastmoney_quant import config
from eastmoney_quant.data_fetch import fetch_tencent_open_prices
from eastmoney_quant.orders import (
    build_buy_recommendations,
    build_initial_buy_recommendations_from_prescreen,
    load_open_prices,
    normalise_open_prices,
    save_order_files,
)
from eastmoney_quant.prediction import load_factor_data, run_prediction, track_logged_predictions
from eastmoney_quant.report import refresh_dashboard_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and track the latest strategy portfolio")
    parser.add_argument(
        "--top-n",
        type=int,
        default=config.LIVE_MAX_HOLDINGS,
        help="maximum holdings (5--10); actual count follows market breadth",
    )
    parser.add_argument("--refresh-factors", action="store_true", help="rebuild factor data from clean_daily.parquet first")
    parser.add_argument("--no-log", action="store_true", help="do not append this run to prediction_log.csv")
    parser.add_argument("--track-only", action="store_true", help="only update prediction_tracking.csv from existing logs")
    parser.add_argument(
        "--orders-only",
        action="store_true",
        help="use the saved latest portfolio and supplied opening prices to create initial-buy orders",
    )
    parser.add_argument("--open-prices-file", help="CSV with code,open_price columns")
    parser.add_argument(
        "--fetch-live-opens",
        action="store_true",
        help="fetch opening prices from Tencent's live quote endpoint instead of reading a CSV",
    )
    parser.add_argument(
        "--open-price",
        action="append",
        default=[],
        metavar="CODE=PRICE",
        help="one opening price, repeat for multiple codes; can be combined with --open-prices-file",
    )
    parser.add_argument("--total-assets", type=float, default=config.ORDER_TOTAL_ASSETS, help="initial account assets in RMB")
    parser.add_argument("--open-premium", type=float, default=config.ORDER_OPEN_PREMIUM, help="maximum premium above open for the buy limit")
    parser.add_argument(
        "--max-gap-from-close",
        type=float,
        default=config.ORDER_MAX_GAP_FROM_CLOSE,
        help="do not buy above this premium to the signal-day close",
    )
    return parser.parse_args()


def _supplied_open_prices(args: argparse.Namespace) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if args.open_prices_file:
        frames.append(load_open_prices(Path(args.open_prices_file)))
    if args.open_price:
        records = []
        for item in args.open_price:
            code, separator, value = item.partition("=")
            if not separator:
                raise ValueError(f"invalid --open-price {item!r}; use CODE=PRICE")
            records.append({"code": code, "open_price": value})
        frames.append(normalise_open_prices(pd.DataFrame(records)))
    if not frames:
        raise ValueError("provide --open-prices-file or at least one --open-price")
    return normalise_open_prices(pd.concat(frames, ignore_index=True))


def _fetch_live_open_prices(candidates: pd.DataFrame) -> pd.DataFrame:
    quotes = fetch_tencent_open_prices(candidates["code"])
    expected_date = pd.to_datetime(candidates["execution_date"].iloc[0]).strftime("%Y%m%d")
    quotes = quotes[quotes["quote_timestamp"].astype(str).str.startswith(expected_date)].copy()
    opens = normalise_open_prices(quotes[["code", "open_price"]])
    expected_codes = set(candidates["code"].astype(str).str.zfill(6))
    missing = sorted(expected_codes - set(opens["code"]))
    if missing:
        raise ValueError(f"live quote has no valid opening price for: {', '.join(missing)}")
    quotes.to_csv(config.LIVE_OPEN_PRICE_PATH, index=False, encoding="utf-8-sig")
    return quotes


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    if args.orders_only:
        if args.track_only:
            raise ValueError("--orders-only cannot be combined with --track-only")
        source_path = config.PRESCREEN_CANDIDATES_PATH if config.PRESCREEN_CANDIDATES_PATH.exists() else config.LATEST_PORTFOLIO_PATH
        if not source_path.exists():
            raise FileNotFoundError("daily pre-screen not found; run `python predict.py` after the latest close first")
        candidates = pd.read_csv(source_path, dtype={"code": str})
        if args.fetch_live_opens:
            supplied_prices = _fetch_live_open_prices(candidates)
            print(f"Saved live opening prices: {config.LIVE_OPEN_PRICE_PATH}")
        else:
            supplied_prices = _supplied_open_prices(args)
        if source_path == config.PRESCREEN_CANDIDATES_PATH:
            orders = build_initial_buy_recommendations_from_prescreen(
                candidates,
                supplied_prices,
                total_assets=args.total_assets,
                open_premium=args.open_premium,
                max_gap_from_close=args.max_gap_from_close,
            )
        else:
            orders = build_buy_recommendations(
                candidates,
                supplied_prices,
                total_assets=args.total_assets,
                open_premium=args.open_premium,
                max_gap_from_close=args.max_gap_from_close,
            )
        if args.fetch_live_opens:
            quote_index = supplied_prices.set_index("code")
            orders["quote_last_price"] = orders["code"].map(quote_index["last_price"])
            orders["quote_timestamp"] = orders["code"].map(quote_index["quote_timestamp"])
        save_order_files(candidates, recommendations=orders)
        refresh_dashboard_data()
        print(f"Saved initial-buy recommendations: {config.BUY_RECOMMENDATION_PATH}")
        print(orders.to_string(index=False))
        return
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
    print(f"Planned initial-buy date: {result.summary['execution_date']}")
    print(f"Saved latest portfolio: {config.LATEST_PORTFOLIO_PATH}")
    print(f"Fill this opening-price template: {config.OPEN_PRICE_TEMPLATE_PATH}")
    print(f"Saved tracking file: {config.PREDICTION_TRACKING_PATH}")
    print()
    print(result.portfolio[["rank", "code", "name", "score", "reference_close", "target_budget"]].to_string(index=False))
    if not result.tracking.empty:
        print()
        print("Tracked prediction returns:")
        display_cols = ["signal_date", "as_of_date", "holding_days", "portfolio_return", "covered_holdings"]
        print(result.tracking[display_cols].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
