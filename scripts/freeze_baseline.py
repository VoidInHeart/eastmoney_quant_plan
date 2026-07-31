from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config


BASELINE_NAME = "baseline_weekly_v1"


def _copy_csv(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"required baseline source is missing: {source}")
    pd.read_csv(source).to_csv(destination, index=False, encoding="utf-8-sig")


def main() -> None:
    config.ensure_directories()
    with config.PERFORMANCE_PATH.open("r", encoding="utf-8") as file:
        performance = json.load(file)
    metrics = {
        "baseline_name": BASELINE_NAME,
        "frozen_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rebalance_frequency": config.REBALANCE_FREQUENCY,
        "top_n": config.TOP_N,
        "rank_buffer": config.HOLDING_RANK_BUFFER,
        "max_replacements_per_rebalance": config.MAX_REPLACEMENTS_PER_REBALANCE,
        "min_holding_days": config.MIN_HOLDING_DAYS,
        "min_score_improvement": config.MIN_SCORE_IMPROVEMENT,
        "position_weight_method": config.POSITION_WEIGHT_METHOD,
        "commission_rate": config.COMMISSION_RATE,
        "stamp_tax_rate": config.STAMP_TAX_RATE,
        "slippage_rate": config.SLIPPAGE_RATE,
        **performance,
    }
    pd.DataFrame([metrics]).to_csv(config.RESULT_DIR / "baseline_metrics.csv", index=False, encoding="utf-8-sig")
    _copy_csv(config.LATEST_PORTFOLIO_PATH, config.RESULT_DIR / "baseline_positions.csv")
    _copy_csv(config.TRADES_PATH, config.RESULT_DIR / "baseline_trades.csv")
    _copy_csv(config.NAV_PATH, config.RESULT_DIR / "baseline_daily_returns.csv")
    _copy_csv(config.BENCHMARK_NAV_PATH, config.RESULT_DIR / "baseline_benchmark.csv")
    manifest = {
        "baseline_name": BASELINE_NAME,
        "files": {
            "metrics": "baseline_metrics.csv",
            "positions": "baseline_positions.csv",
            "trades": "baseline_trades.csv",
            "daily_returns": "baseline_daily_returns.csv",
            "benchmark": "baseline_benchmark.csv",
        },
    }
    (config.RESULT_DIR / "baseline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Frozen baseline: {BASELINE_NAME}")


if __name__ == "__main__":
    main()
