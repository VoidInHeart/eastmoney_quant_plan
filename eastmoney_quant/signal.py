from __future__ import annotations

import pandas as pd

from . import config


def get_rebalance_dates(df: pd.DataFrame) -> list[pd.Timestamp]:
    """Use the last available trading day of each month as the rebalance date."""
    dates = pd.Series(pd.to_datetime(df["date"].dropna().unique())).sort_values()
    if dates.empty:
        return []
    month_ends = dates.groupby(dates.dt.to_period("M")).max()
    return month_ends.tolist()


def filter_stock_pool(
    df: pd.DataFrame,
    min_listing_days: int = config.MIN_LISTING_DAYS,
    min_amount_ma20: float = config.MIN_AMOUNT_MA20,
) -> pd.DataFrame:
    """Apply the base tradable-universe filters described in the plan."""
    result = df.copy()
    name = result.get("name", pd.Series("", index=result.index)).fillna("").astype(str)
    mask = (
        result["score"].notna()
        & result["open"].notna()
        & result["close"].notna()
        & (result["open"] > 0)
        & (result["close"] > 0)
        & (result["listing_days"] >= min_listing_days)
        & (result["amount_ma20"] >= min_amount_ma20)
        & ~name.str.upper().str.contains("ST", regex=False)
        & ~name.str.contains("退", regex=False)
    )
    return result.loc[mask].copy()


def build_monthly_signal(df: pd.DataFrame, top_n: int = config.TOP_N) -> pd.DataFrame:
    """Select the top-scoring stocks on each monthly rebalance date."""
    frames: list[pd.DataFrame] = []
    for date in get_rebalance_dates(df):
        daily = df[df["date"] == date]
        pool = filter_stock_pool(daily)
        if pool.empty:
            continue
        selected = pool.sort_values("score", ascending=False).head(top_n).copy()
        selected["weight"] = 1 / len(selected)
        frames.append(selected[["date", "code", "name", "score", "weight"]])
    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "score", "weight"])
    return pd.concat(frames, ignore_index=True).sort_values(["date", "score"], ascending=[True, False])
