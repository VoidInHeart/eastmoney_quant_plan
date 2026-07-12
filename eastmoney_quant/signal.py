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


def get_daily_rebalance_dates(
    df: pd.DataFrame,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    reserve_latest: bool = True,
) -> list[pd.Timestamp]:
    """Return recent daily signal dates while reserving one future open for pricing.

    A signal formed after a close is executed on the next opening.  The latest
    available close therefore cannot be a completed signal period yet.  We keep
    one extra signal date so a 252-day evaluation produces roughly 252 returns.
    """
    dates = pd.Series(pd.to_datetime(df["date"].dropna().unique())).sort_values()
    if len(dates) < 3:
        return []
    usable_dates = dates.iloc[:-1] if reserve_latest else dates
    return usable_dates.iloc[-(trading_days + 1) :].tolist()


def filter_stock_pool(
    df: pd.DataFrame,
    min_listing_days: int = config.MIN_LISTING_DAYS,
    min_amount_ma20: float = config.MIN_AMOUNT_MA20,
    max_vol_quantile: float | None = config.MAX_VOL_QUANTILE,
) -> pd.DataFrame:
    """Apply tradability and risk filters before ranking stocks."""
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
        & ~name.str.contains("\u9000", regex=False)
    )
    if "trend_120" in result.columns:
        mask &= result["trend_120"].notna() & (result["trend_120"] > config.MIN_TREND_120)
    if "drawdown_120" in result.columns:
        mask &= result["drawdown_120"].notna() & (result["drawdown_120"] > config.MIN_DRAWDOWN_120)
    if max_vol_quantile is not None and "vol_60" in result.columns:
        vol_cut = result["vol_60"].quantile(max_vol_quantile)
        mask &= result["vol_60"].notna() & (result["vol_60"] >= vol_cut)
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


def _build_controlled_daily_selection(
    pool: pd.DataFrame,
    previous_codes: list[str],
    holding_ages: dict[str, int],
    top_n: int,
    rank_buffer: int,
    max_replacements: int,
    min_holding_days: int,
) -> tuple[pd.DataFrame, list[str], dict[str, int]]:
    """Apply rank-buffer and turnover limits to a daily cross-sectional signal."""
    ranked = pool.sort_values(["score", "code"], ascending=[False, True]).copy()
    ranked["code"] = ranked["code"].astype(str)
    rank_by_code = {code: rank for rank, code in enumerate(ranked["code"], start=1)}
    available = set(rank_by_code)
    previous_codes = [code for code in previous_codes if code in available]
    previous_set = set(previous_codes)

    # Retain recent holdings and names that are still inside the wider rank band.
    kept = [
        code
        for code in previous_codes
        if holding_ages.get(code, 0) < min_holding_days or rank_by_code[code] <= top_n + rank_buffer
    ]

    selected = list(dict.fromkeys(kept))[:top_n]
    remaining_slots = top_n - len(selected)
    new_entries = [code for code in ranked["code"] if code not in previous_set]
    selected.extend(new_entries[: min(remaining_slots, max_replacements)])

    # When replacement capacity is exhausted, retain lower-ranked existing names
    # rather than leaving cash unintentionally uninvested.
    for code in previous_codes:
        if len(selected) >= top_n:
            break
        if code not in selected:
            selected.append(code)
    for code in ranked["code"]:
        if len(selected) >= top_n:
            break
        if code not in selected:
            selected.append(code)

    selected = selected[:top_n]
    selected_frame = ranked.set_index("code", drop=False).loc[selected].copy()
    selected_frame["rank"] = selected_frame["code"].map(rank_by_code).astype(int)
    selected_frame["weight"] = 1 / len(selected_frame)
    next_holding_ages = {
        code: holding_ages.get(code, 0) + 1 if code in previous_set else 1 for code in selected
    }
    selected_frame["holding_days"] = selected_frame["code"].map(next_holding_ages).astype(int)
    return selected_frame, selected, next_holding_ages


def build_daily_signal(
    df: pd.DataFrame,
    top_n: int = config.TOP_N,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    rank_buffer: int = config.HOLDING_RANK_BUFFER,
    max_replacements: int = config.MAX_DAILY_REPLACEMENTS,
    min_holding_days: int = config.MIN_HOLDING_DAYS,
    reserve_latest: bool = True,
) -> pd.DataFrame:
    """Build a recent daily signal with rank buffering and bounded turnover."""
    frames: list[pd.DataFrame] = []
    previous_codes: list[str] = []
    holding_ages: dict[str, int] = {}
    for date in get_daily_rebalance_dates(df, trading_days=trading_days, reserve_latest=reserve_latest):
        daily = df[df["date"] == date]
        pool = filter_stock_pool(daily)
        if pool.empty:
            continue
        selected, previous_codes, holding_ages = _build_controlled_daily_selection(
            pool,
            previous_codes=previous_codes,
            holding_ages=holding_ages,
            top_n=top_n,
            rank_buffer=rank_buffer,
            max_replacements=max_replacements,
            min_holding_days=min_holding_days,
        )
        selected["date"] = date
        frames.append(selected[["date", "code", "name", "score", "rank", "holding_days", "weight"]])
    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "score", "rank", "holding_days", "weight"])
    return pd.concat(frames, ignore_index=True).sort_values(["date", "rank"]).reset_index(drop=True)


def build_rebalance_signal(df: pd.DataFrame, top_n: int = config.TOP_N) -> pd.DataFrame:
    """Build the configured strategy signal without changing factor history."""
    if config.REBALANCE_FREQUENCY == "daily":
        return build_daily_signal(df, top_n=top_n)
    if config.REBALANCE_FREQUENCY == "monthly":
        return build_monthly_signal(df, top_n=top_n)
    raise ValueError(f"unsupported rebalance frequency: {config.REBALANCE_FREQUENCY}")
