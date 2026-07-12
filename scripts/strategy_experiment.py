from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.signal import get_rebalance_dates


FACTOR_SETS = {
    "base": {
        "mom_60_20": 0.40,
        "rev_5": 0.20,
        "vol_20": 0.25,
        "liq_20": 0.15,
    },
    "balanced": {
        "mom_120_20": 0.30,
        "trend_120": 0.20,
        "rev_10": 0.15,
        "vol_60": 0.25,
        "drawdown_120": 0.10,
    },
    "defensive": {
        "trend_120": 0.25,
        "vol_60": 0.35,
        "drawdown_120": 0.25,
        "rev_10": 0.15,
    },
    "momentum_quality": {
        "mom_120_20": 0.35,
        "mom_240_20": 0.20,
        "trend_120": 0.20,
        "vol_60": 0.15,
        "drawdown_120": 0.10,
    },
}


def rank_score(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 2 - 1


def add_experiment_factors(df: pd.DataFrame) -> pd.DataFrame:
    result = df.sort_values(["code", "date"]).copy()
    by_code = result.groupby("code", group_keys=False)
    result["ret"] = by_code["close"].pct_change()
    result["mom_60_20"] = by_code["close"].transform(lambda s: s.shift(20) / s.shift(60) - 1)
    result["mom_120_20"] = by_code["close"].transform(lambda s: s.shift(20) / s.shift(120) - 1)
    result["mom_240_20"] = by_code["close"].transform(lambda s: s.shift(20) / s.shift(240) - 1)
    result["rev_5"] = by_code["close"].transform(lambda s: -(s / s.shift(5) - 1))
    result["rev_10"] = by_code["close"].transform(lambda s: -(s / s.shift(10) - 1))
    result["vol_20"] = -by_code["ret"].transform(lambda s: s.rolling(20, min_periods=20).std())
    result["vol_60"] = -by_code["ret"].transform(lambda s: s.rolling(60, min_periods=60).std())
    ma120 = by_code["close"].transform(lambda s: s.rolling(120, min_periods=120).mean())
    high120 = by_code["close"].transform(lambda s: s.rolling(120, min_periods=120).max())
    amount_ma20 = by_code["amount"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    result["trend_120"] = result["close"] / ma120 - 1
    result["drawdown_120"] = result["close"] / high120 - 1
    result["liq_20"] = np.log(amount_ma20.where(amount_ma20 > 0))
    result["amount_ma20"] = amount_ma20
    result["listing_days"] = by_code.cumcount() + 1
    return result


def build_signal(
    df: pd.DataFrame,
    weights: dict[str, float],
    top_n: int,
    min_listing_days: int,
    min_amount_ma20: float,
    trend_filter: bool,
    max_vol_quantile: float | None,
) -> pd.DataFrame:
    factor_cols = list(weights)
    work = df.copy()
    for col in factor_cols:
        work[f"{col}_rank"] = work.groupby("date")[col].transform(rank_score)
    score = pd.Series(0.0, index=work.index)
    for col, weight in weights.items():
        score = score + weight * work[f"{col}_rank"]
    work["score"] = score

    frames = []
    for date in get_rebalance_dates(work):
        daily = work[work["date"] == date].copy()
        name = daily.get("name", pd.Series("", index=daily.index)).fillna("").astype(str)
        mask = (
            daily["score"].notna()
            & daily["open"].notna()
            & daily["close"].notna()
            & (daily["open"] > 0)
            & (daily["close"] > 0)
            & (daily["listing_days"] >= min_listing_days)
            & (daily["amount_ma20"] >= min_amount_ma20)
            & ~name.str.upper().str.contains("ST", regex=False)
            & ~name.str.contains("退", regex=False)
        )
        if trend_filter:
            mask &= daily["trend_120"] > -0.15
            mask &= daily["drawdown_120"] > -0.45
        if max_vol_quantile is not None and "vol_60" in daily:
            vol_cut = daily["vol_60"].quantile(max_vol_quantile)
            mask &= daily["vol_60"] >= vol_cut
        pool = daily.loc[mask]
        if pool.empty:
            continue
        selected = pool.sort_values("score", ascending=False).head(top_n).copy()
        selected["weight"] = 1 / len(selected)
        frames.append(selected[["date", "code", "name", "score", "weight"]])
    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "score", "weight"])
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    clean = pd.read_parquet(config.CLEAN_DAILY_PATH)
    factors = add_experiment_factors(clean)
    rows = []
    top_ns = [20, 30, 50, 80]
    trend_filters = [False, True]
    vol_filters = [None, 0.25]
    for name, weights in FACTOR_SETS.items():
        for top_n, trend_filter, vol_filter in itertools.product(top_ns, trend_filters, vol_filters):
            signal = build_signal(
                factors,
                weights=weights,
                top_n=top_n,
                min_listing_days=180,
                min_amount_ma20=20_000_000,
                trend_filter=trend_filter,
                max_vol_quantile=vol_filter,
            )
            if signal.empty:
                continue
            result = run_backtest(factors, signal)
            perf = result["performance"]
            rows.append(
                {
                    "name": name,
                    "top_n": top_n,
                    "trend_filter": trend_filter,
                    "vol_filter": vol_filter,
                    "cum": perf["cumulative_return"],
                    "ann": perf["annual_return"],
                    "mdd": perf["max_drawdown"],
                    "sharpe": perf["sharpe"],
                    "rel": perf["relative_return"],
                    "bench": perf["benchmark_cumulative_return"],
                    "turnover": perf["avg_turnover"],
                }
            )
            print(rows[-1], flush=True)
    out = pd.DataFrame(rows).sort_values(["rel", "ann", "mdd"], ascending=[False, False, False])
    print("\nTOP RESULTS")
    print(out.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
