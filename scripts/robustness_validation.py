from __future__ import annotations

import gc
from dataclasses import asdict, dataclass
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.factor import build_windowed_factors
from eastmoney_quant.signal import build_frequency_signal, build_interval_signal


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


@dataclass(frozen=True)
class StrategySpec:
    name: str
    interval_days: int
    top_n: int = 20
    rank_buffer: int = 50
    max_replacements: int = 2
    min_holding_days: int = 15
    min_score_improvement: float = 0.05


CURRENT_SPEC = StrategySpec("current_5d", interval_days=5)
CANDIDATES = [
    CURRENT_SPEC,
    StrategySpec("interval_7d", interval_days=7),
    StrategySpec("interval_10d", interval_days=10),
    StrategySpec("interval_14d", interval_days=14),
    StrategySpec("top_n_16", interval_days=5, top_n=16),
    StrategySpec("top_n_24", interval_days=5, top_n=24),
    StrategySpec("threshold_003", interval_days=5, min_score_improvement=0.03),
    StrategySpec("threshold_007", interval_days=5, min_score_improvement=0.07),
]

TRAIN = ("2021-07-01", "2023-12-29")
VALIDATION = ("2024-01-02", "2024-12-31")
TEST = ("2025-01-02", "2026-07-10")
FULL = ("2021-07-01", "2026-07-10")


def load_factor_range(start: str, end: str) -> pd.DataFrame:
    data = pd.read_parquet(
        config.FACTOR_DAILY_PATH,
        columns=FACTOR_COLUMNS,
        filters=[("date", ">=", pd.Timestamp(start)), ("date", "<=", pd.Timestamp(end))],
    )
    data["date"] = pd.to_datetime(data["date"])
    return data


def window_trading_days(data: pd.DataFrame) -> int:
    return max(2, int(data["date"].nunique()) - 1)


def run_spec(
    data: pd.DataFrame,
    spec: StrategySpec,
    one_way_cost_bps: float | None = None,
    use_calendar_week: bool = False,
) -> dict[str, object]:
    trading_days = window_trading_days(data)
    if use_calendar_week:
        signal = build_frequency_signal(
            data,
            frequency="weekly",
            trading_days=trading_days,
            top_n=spec.top_n,
            rank_buffer=spec.rank_buffer,
            max_replacements=spec.max_replacements,
            min_holding_days=spec.min_holding_days,
            min_score_improvement=spec.min_score_improvement,
        )
        annualization_periods = None
    else:
        signal = build_interval_signal(
            data,
            interval_days=spec.interval_days,
            trading_days=trading_days,
            top_n=spec.top_n,
            rank_buffer=spec.rank_buffer,
            max_replacements=spec.max_replacements,
            min_holding_days=spec.min_holding_days,
            min_score_improvement=spec.min_score_improvement,
        )
        annualization_periods = 252 / spec.interval_days

    if one_way_cost_bps is None:
        result = run_backtest(data, signal, annualization_periods=annualization_periods)
    else:
        # Symmetric all-in one-way cost.  This isolates cost sensitivity from the
        # A-share-specific commission/stamp-tax/slippage decomposition.
        result = run_backtest(
            data,
            signal,
            commission_rate=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=one_way_cost_bps / 10_000,
            annualization_periods=annualization_periods,
        )
    performance = result["performance"]
    return {
        **asdict(spec),
        "signal_dates": int(signal["date"].nunique()),
        "annual_turnover": performance["avg_turnover"] * performance["annualization_periods"],
        "cumulative_return": performance["cumulative_return"],
        "annual_return": performance["annual_return"],
        "relative_return": performance["relative_return"],
        "sharpe": performance["sharpe"],
        "max_drawdown": performance["max_drawdown"],
        "total_cost": performance["total_cost"],
        "annualization_periods": performance["annualization_periods"],
    }


def write_csv(name: str, rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame.to_csv(config.RESULT_DIR / name, index=False, encoding="utf-8-sig")
    return frame


def run_factor_window_sensitivity() -> pd.DataFrame:
    clean = pd.read_parquet(config.CLEAN_DAILY_PATH)
    rows = []
    window_specs = [
        ("fast_80pct", dict(momentum_lookback=96, momentum_skip=16, trend_window=96, reversal_window=8, volatility_window=48, drawdown_window=96)),
        ("base_100pct", dict(momentum_lookback=120, momentum_skip=20, trend_window=120, reversal_window=10, volatility_window=60, drawdown_window=120)),
        ("slow_120pct", dict(momentum_lookback=144, momentum_skip=24, trend_window=144, reversal_window=12, volatility_window=72, drawdown_window=144)),
    ]
    for name, windows in window_specs:
        factors = build_windowed_factors(clean, **windows)
        test_factors = factors[(factors["date"] >= pd.Timestamp(TEST[0])) & (factors["date"] <= pd.Timestamp(TEST[1]))]
        row = run_spec(test_factors, CURRENT_SPEC, one_way_cost_bps=20, use_calendar_week=True)
        row.update({"factor_window": name, **windows})
        rows.append(row)
        print(f"factor window {name}: {row}", flush=True)
        del factors, test_factors
        gc.collect()
    del clean
    gc.collect()
    return write_csv("factor_window_sensitivity.csv", rows)


def main() -> None:
    # Parameter candidates are selected only on the training period under a
    # demanding 20 bps one-way cost. Validation and test do not influence choice.
    train_data = load_factor_range(*TRAIN)
    train_rows = [run_spec(train_data, spec, one_way_cost_bps=20) for spec in CANDIDATES]
    train = write_csv("validation_train_candidates.csv", train_rows)
    eligible = train[train["annual_turnover"].between(5.0, 10.0)]
    ranked = eligible if not eligible.empty else train
    selected_name = ranked.sort_values(["relative_return", "sharpe"], ascending=False).iloc[0]["name"]
    selected = next(spec for spec in CANDIDATES if spec.name == selected_name)
    print(f"selected from training only: {selected}", flush=True)
    del train_data
    gc.collect()

    split_rows = []
    for split_name, dates in [("train", TRAIN), ("validation", VALIDATION), ("test", TEST)]:
        split_data = load_factor_range(*dates)
        row = run_spec(split_data, selected, one_way_cost_bps=20)
        row["split"] = split_name
        row["start"] = dates[0]
        row["end"] = dates[1]
        split_rows.append(row)
        del split_data
        gc.collect()
    write_csv("validation_time_splits.csv", split_rows)

    test_data = load_factor_range(*TEST)
    cost_rows = []
    for cost_bps in [5, 10, 20, 30]:
        row = run_spec(test_data, selected, one_way_cost_bps=cost_bps)
        row["one_way_cost_bps"] = cost_bps
        cost_rows.append(row)
    write_csv("cost_pressure_test.csv", cost_rows)

    stability_rows = []
    for spec in CANDIDATES:
        row = run_spec(test_data, spec, one_way_cost_bps=20)
        row["stability_group"] = "interval_topn_threshold"
        stability_rows.append(row)
    write_csv("parameter_stability.csv", stability_rows)
    del test_data
    gc.collect()

    full_data = load_factor_range(*FULL)
    full_row = run_spec(full_data, CURRENT_SPEC, one_way_cost_bps=20, use_calendar_week=True)
    full_row.update({"start": FULL[0], "end": FULL[1], "scope": "five_year_extended"})
    write_csv("extended_backtest.csv", [full_row])
    del full_data
    gc.collect()

    run_factor_window_sensitivity()
    print(f"Validation outputs saved to: {config.RESULT_DIR}")


if __name__ == "__main__":
    main()
