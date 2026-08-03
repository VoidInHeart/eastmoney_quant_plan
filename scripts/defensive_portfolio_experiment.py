"""Compare a small, cash-aware defensive candidate with the frozen baseline.

This is a research experiment, not a replacement for the baseline.  It keeps
the existing five-factor score and weekly T+1 execution, but converts market
breadth into a pre-specified 5/8/10 holding count and 35%/65%/100% exposure.
Unallocated weight is cash earning zero return in the simplified backtest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eastmoney_quant import config
from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.signal import build_frequency_signal


PERIODS = {
    "train": ("2021-07-01", "2023-12-29"),
    "validation": ("2024-01-02", "2024-12-31"),
    "observed_test": ("2025-01-02", "2026-07-31"),
}
SMALL_PORTFOLIOS = [
    ("top10_weekly", "weekly", 10, "equal"),
    ("top8_weekly", "weekly", 8, "equal"),
    ("top5_weekly", "weekly", 5, "equal"),
    ("top10_biweekly", "biweekly", 10, "equal"),
    ("top10_monthly", "monthly", 10, "equal"),
    ("top8_weekly_inverse_vol", "weekly", 8, "inverse_volatility"),
]
FACTOR_COLUMNS = ["date", "code", "name", "open", "close", "amount_ma20", "listing_days", "trend_120", "drawdown_120", "vol_60", "score"]


def load_period(start: str, end: str) -> pd.DataFrame:
    data = pd.read_parquet(
        config.FACTOR_DAILY_PATH,
        columns=FACTOR_COLUMNS,
        filters=[("date", ">=", pd.Timestamp(start)), ("date", "<=", pd.Timestamp(end))],
    )
    data["date"] = pd.to_datetime(data["date"])
    data["code"] = data["code"].astype(str).str.zfill(6)
    return data


def defensive_signal(data: pd.DataFrame) -> pd.DataFrame:
    """Apply a pre-specified breadth regime to the existing Top-10 signal."""
    trading_days = max(2, int(data["date"].nunique()) - 1)
    base = build_frequency_signal(data, frequency="weekly", trading_days=trading_days, top_n=10)
    breadth = data.groupby("date")["trend_120"].apply(lambda value: float((value > 0).mean()))
    result = base.copy()
    result["breadth"] = result["date"].map(breadth).fillna(0.0)
    result["target_holdings"] = pd.cut(
        result["breadth"], bins=[-1, 0.40, 0.60, 2], labels=[5, 8, 10]
    ).astype(int)
    result["exposure"] = pd.cut(
        result["breadth"], bins=[-1, 0.40, 0.60, 2], labels=[0.35, 0.65, 1.00]
    ).astype(float)
    result = result.sort_values(["date", "rank"]).copy()
    result["selection_order"] = result.groupby("date").cumcount() + 1
    result = result.loc[result["selection_order"] <= result["target_holdings"]].copy()
    result["weight"] = result["exposure"] / result.groupby("date")["code"].transform("size")
    return result


def evaluate(data: pd.DataFrame, name: str, signal: pd.DataFrame) -> dict[str, object]:
    result = run_backtest(data, signal, annualization_periods=52)
    metrics = dict(result["performance"])
    metrics.update(
        {
            "strategy": name,
            "signal_dates": int(signal["date"].nunique()),
            "mean_holdings": float(signal.groupby("date")["code"].size().mean()),
            "mean_exposure": float(signal.groupby("date")["weight"].sum().mean()),
        }
    )
    return metrics


def small_portfolio_signal(
    data: pd.DataFrame,
    frequency: str,
    top_n: int,
    weight_method: str,
) -> pd.DataFrame:
    """Use the existing controlled signal with a 5--10-name mandate."""
    days = max(2, int(data["date"].nunique()) - 1)
    return build_frequency_signal(
        data,
        frequency=frequency,
        trading_days=days,
        top_n=top_n,
        position_weight_method=weight_method,
    )


def main() -> None:
    records: list[dict[str, object]] = []
    for period, (start, end) in PERIODS.items():
        data = load_period(start, end)
        days = max(2, int(data["date"].nunique()) - 1)
        baseline = build_frequency_signal(data, frequency="weekly", trading_days=days, top_n=20)
        records.append({"period": period, **evaluate(data, "baseline_top20", baseline)})
        for name, frequency, top_n, weight_method in SMALL_PORTFOLIOS:
            records.append(
                {
                    "period": period,
                    **evaluate(
                        data,
                        name,
                        small_portfolio_signal(data, frequency, top_n, weight_method),
                    ),
                }
            )
        records.append({"period": period, **evaluate(data, "defensive_5_10_cash", defensive_signal(data))})
    output = config.RESULT_DIR / "defensive_portfolio_experiment.csv"
    pd.DataFrame(records).to_csv(output, index=False, encoding="utf-8-sig")
    print(pd.DataFrame(records)[["period", "strategy", "cumulative_return", "relative_return", "max_drawdown", "total_cost", "mean_holdings", "mean_exposure"]].to_string(index=False))
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
