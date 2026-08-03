"""Validate the research-only 5--10-name adaptive candidate from local cache.

No download is performed.  The current production baseline is not modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eastmoney_quant import config
from eastmoney_quant.adaptive_candidate import AdaptiveCandidateConfig, run_adaptive_walk_forward
from eastmoney_quant.prediction import load_factor_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the research-only 5--10-name adaptive candidate")
    parser.add_argument("--start-date", required=True, help="Initial close-time signal date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Final mark date; defaults to latest factor date")
    parser.add_argument("--tag", default="adaptive_candidate", help="Prefix appended to result filenames")
    parser.add_argument("--refresh-factors", action="store_true", help="Rebuild factors from local clean cache before validation")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    factors = load_factor_data(refresh=args.refresh_factors)
    factors["date"] = pd.to_datetime(factors["date"])
    start_date = pd.Timestamp(args.start_date).normalize()
    end_date = pd.Timestamp(args.end_date).normalize() if args.end_date else factors["date"].max()
    result = run_adaptive_walk_forward(
        factors,
        start_date=start_date,
        end_date=end_date,
        candidate=AdaptiveCandidateConfig(),
    )
    stem = f"{args.tag}_{start_date:%Y%m%d}_{end_date:%Y%m%d}"
    outputs = {
        "nav": config.RESULT_DIR / f"{stem}_nav.csv",
        "benchmark": config.RESULT_DIR / f"{stem}_benchmark.csv",
        "trades": config.RESULT_DIR / f"{stem}_trades.csv",
        "schedule": config.RESULT_DIR / f"{stem}_schedule.csv",
        "summary": config.RESULT_DIR / f"{stem}_summary.json",
    }
    for key in ("nav", "benchmark", "trades", "schedule"):
        result[key].to_csv(outputs[key], index=False, encoding="utf-8-sig")
    outputs["summary"].write_text(
        json.dumps(result["performance"], ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    performance = result["performance"]
    print(f"Candidate: {performance['start_signal_date']} close -> {performance['end_date']} open")
    print(f"Strategy return: {performance['cumulative_return']:.2%}")
    print(f"Benchmark return: {performance['benchmark_cumulative_return']:.2%}")
    print(f"Excess return: {performance['excess_cumulative_return']:.2%}")
    print(f"Max drawdown: {performance['max_drawdown']:.2%}")
    print(f"Total cost: {performance['total_cost']:.2%}")
    print(f"Daily risk reductions: {performance['daily_risk_reductions']}")
    print(f"Summary: {outputs['summary']}")


if __name__ == "__main__":
    main()
