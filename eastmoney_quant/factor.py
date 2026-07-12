from __future__ import annotations

import numpy as np
import pandas as pd


FACTOR_COLS = ["mom_120_20", "trend_120", "rev_10", "vol_60", "drawdown_120"]
FACTOR_WEIGHTS = {
    "mom_120_20_rank": 0.30,
    "trend_120_rank": 0.20,
    "rev_10_rank": 0.15,
    "vol_60_rank": 0.25,
    "drawdown_120_rank": 0.10,
}


def add_basic_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Add close-to-close daily returns by stock."""
    result = df.sort_values(["code", "date"]).copy()
    result["ret"] = result.groupby("code")["close"].pct_change()
    return result


def build_factors(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate the optimized momentum, trend, reversal and risk-control factors."""
    result = add_basic_returns(df)
    by_code = result.groupby("code", group_keys=False)
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
    result["amount_ma20"] = amount_ma20
    result["liq_20"] = np.log(amount_ma20.where(amount_ma20 > 0))
    result["trend_120"] = result["close"] / ma120 - 1
    result["drawdown_120"] = result["close"] / high120 - 1
    result["listing_days"] = by_code.cumcount() + 1
    return build_score(cross_sectional_rank_score(result, FACTOR_COLS))


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def cross_sectional_zscore(df: pd.DataFrame, factor_cols: list[str]) -> pd.DataFrame:
    """Cross-sectionally z-score each factor on each trade date."""
    result = df.copy()
    for col in factor_cols:
        result[f"{col}_z"] = result.groupby("date")[col].transform(_zscore)
    return result


def _rank_score(s: pd.Series) -> pd.Series:
    ranked = s.rank(pct=True)
    return ranked * 2 - 1


def cross_sectional_rank_score(df: pd.DataFrame, factor_cols: list[str]) -> pd.DataFrame:
    """Cross-sectionally rank each factor on each trade date into [-1, 1]."""
    result = df.copy()
    for col in factor_cols:
        result[f"{col}_rank"] = result.groupby("date")[col].transform(_rank_score)
    return result


def build_score(df: pd.DataFrame) -> pd.DataFrame:
    """Build the weighted composite score."""
    result = df.copy()
    score = pd.Series(0.0, index=result.index)
    for col, weight in FACTOR_WEIGHTS.items():
        score = score + weight * result[col]
    result["score"] = score
    return result
