from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.signal import build_frequency_signal


@dataclass(frozen=True)
class Experiment:
    name: str
    frequency: str
    rank_buffer: int
    max_replacements: int
    min_holding_days: int
    min_score_improvement: float
    weight_method: str = "equal"


EXPERIMENTS = [
    Experiment("daily_current", "daily", 20, 4, 5, 0.00),
    Experiment("daily_guarded", "daily", 35, 2, 10, 0.03),
    Experiment("daily_strict", "daily", 50, 1, 15, 0.05),
    Experiment("daily_guarded_rank_tilt", "daily", 35, 2, 10, 0.03, "rank_tilt"),
    Experiment("daily_guarded_inverse_vol", "daily", 35, 2, 10, 0.03, "inverse_volatility"),
    Experiment("weekly_current", "weekly", 20, 6, 5, 0.00),
    Experiment("weekly_guarded", "weekly", 35, 3, 10, 0.03),
    Experiment("weekly_strict", "weekly", 50, 2, 15, 0.05),
    Experiment("weekly_guarded_rank_tilt", "weekly", 35, 3, 10, 0.03, "rank_tilt"),
    Experiment("weekly_strict_rank_tilt", "weekly", 50, 2, 15, 0.05, "rank_tilt"),
    Experiment("weekly_strict_inverse_vol", "weekly", 50, 2, 15, 0.05, "inverse_volatility"),
    Experiment("biweekly_current", "biweekly", 20, 10, 5, 0.00),
    Experiment("biweekly_guarded", "biweekly", 40, 5, 15, 0.03),
    Experiment("biweekly_guarded_rank_tilt", "biweekly", 40, 5, 15, 0.03, "rank_tilt"),
    Experiment("monthly_current", "monthly", 0, 20, 0, 0.00),
    Experiment("monthly_guarded", "monthly", 35, 8, 15, 0.03),
    Experiment("monthly_guarded_rank_tilt", "monthly", 35, 8, 15, 0.03, "rank_tilt"),
]


FACTOR_COLUMNS = [
    "date",
    "code",
    "name",
    "open",
    "close",
    "amount_ma20",
    "listing_days",
    "trend_120",
    "drawdown_120",
    "vol_60",
    "score",
]


def load_recent_factor_data() -> pd.DataFrame:
    all_dates = pd.read_parquet(config.FACTOR_DAILY_PATH, columns=["date"])
    dates = pd.Series(pd.to_datetime(all_dates["date"].dropna().unique())).sort_values().reset_index(drop=True)
    if len(dates) < config.BACKTEST_TRADING_DAYS + 3:
        raise ValueError("not enough factor dates for the configured recent-window experiment")
    start_date = dates.iloc[-(config.BACKTEST_TRADING_DAYS + 3)]
    recent = pd.read_parquet(
        config.FACTOR_DAILY_PATH,
        columns=FACTOR_COLUMNS,
        filters=[("date", ">=", start_date)],
    )
    recent["date"] = pd.to_datetime(recent["date"])
    return recent


def run_experiment(factor_data: pd.DataFrame, experiment: Experiment) -> dict[str, object]:
    signal = build_frequency_signal(
        factor_data,
        frequency=experiment.frequency,
        rank_buffer=experiment.rank_buffer,
        max_replacements=experiment.max_replacements,
        min_holding_days=experiment.min_holding_days,
        min_score_improvement=experiment.min_score_improvement,
        position_weight_method=experiment.weight_method,
    )
    result = run_backtest(factor_data, signal)
    performance = result["performance"]
    annual_turnover = performance["avg_turnover"] * performance["annualization_periods"]
    return {
        **asdict(experiment),
        "signal_dates": int(signal["date"].nunique()),
        "backtest_start": result["nav"]["date"].min().date().isoformat(),
        "backtest_end": result["nav"]["date"].max().date().isoformat(),
        "annual_turnover": annual_turnover,
        "cumulative_return": performance["cumulative_return"],
        "annual_return": performance["annual_return"],
        "relative_return": performance["relative_return"],
        "sharpe": performance["sharpe"],
        "max_drawdown": performance["max_drawdown"],
        "total_cost": performance["total_cost"],
    }


def main() -> None:
    factor_data = load_recent_factor_data()
    rows = []
    for experiment in EXPERIMENTS:
        row = run_experiment(factor_data, experiment)
        rows.append(row)
        print(row, flush=True)

    result = pd.DataFrame(rows)
    result["turnover_in_target"] = result["annual_turnover"].between(5.0, 10.0)
    result = result.sort_values(
        ["turnover_in_target", "relative_return", "annual_return", "max_drawdown"],
        ascending=[False, False, False, False],
    )
    output_path = config.RESULT_DIR / "turnover_experiment.csv"
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print("\nTOP RESULTS")
    print(result.head(10).to_string(index=False))
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
