"""Run the frozen signal through the stricter pending-order execution engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.execution import run_executable_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test signal execution feasibility with pending orders")
    parser.add_argument("--max-order-attempts", type=int, default=5, help="sessions before a pending order is cancelled")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    daily = pd.read_parquet(config.FACTOR_DAILY_PATH, columns=["date", "code", "open", "close"])
    signal = pd.read_parquet(config.SIGNAL_PATH)
    suspensions = pd.read_parquet(config.SUSPENSION_PATH) if config.SUSPENSION_PATH.exists() else None
    result = run_executable_backtest(
        daily,
        signal,
        suspension_events=suspensions,
        max_order_attempts=args.max_order_attempts,
    )
    result["nav"].to_csv(config.RESULT_DIR / "execution_feasibility_nav.csv", index=False, encoding="utf-8-sig")
    result["trades"].to_csv(config.RESULT_DIR / "execution_feasibility_trades.csv", index=False, encoding="utf-8-sig")
    result["order_events"].to_csv(config.RESULT_DIR / "execution_order_events.csv", index=False, encoding="utf-8-sig")
    result["pending_orders"].to_csv(config.RESULT_DIR / "execution_pending_orders.csv", index=False, encoding="utf-8-sig")
    with (config.RESULT_DIR / "execution_feasibility_summary.json").open("w", encoding="utf-8") as file:
        json.dump(result["performance"], file, ensure_ascii=False, indent=2)
    print(json.dumps(result["performance"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
