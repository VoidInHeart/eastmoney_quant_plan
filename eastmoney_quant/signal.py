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


def get_frequency_rebalance_dates(
    df: pd.DataFrame,
    frequency: str,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    reserve_latest: bool = True,
) -> list[pd.Timestamp]:
    """Build recent daily, weekly, biweekly or monthly signal dates.

    The final usable date is always retained as a terminal mark.  Its next open
    lets the backtest value the last completed holding period consistently across
    all rebalance frequencies.
    """
    if frequency == "daily":
        return get_daily_rebalance_dates(df, trading_days=trading_days, reserve_latest=reserve_latest)

    dates = pd.Series(pd.to_datetime(df["date"].dropna().unique())).sort_values()
    usable_dates = dates.iloc[:-1] if reserve_latest else dates
    if usable_dates.empty:
        return []
    window = usable_dates.iloc[-(trading_days + 1) :]
    if frequency == "weekly":
        selected = window.groupby(window.dt.to_period("W-FRI")).max()
    elif frequency == "biweekly":
        weekly_dates = window.groupby(window.dt.to_period("W-FRI")).max().tolist()
        selected = pd.Series(weekly_dates[::2])
    elif frequency == "monthly":
        selected = window.groupby(window.dt.to_period("M")).max()
    else:
        raise ValueError(f"unsupported rebalance frequency: {frequency}")

    terminal_date = window.iloc[-1]
    selected_dates = list(pd.to_datetime(selected).sort_values().unique())
    if terminal_date not in selected_dates:
        selected_dates.append(terminal_date)
    return sorted(selected_dates)


def get_interval_rebalance_dates(
    df: pd.DataFrame,
    interval_days: int,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    reserve_latest: bool = True,
) -> list[pd.Timestamp]:
    """Return signal dates spaced by an exact number of trading days."""
    if interval_days <= 0:
        raise ValueError("interval_days must be positive")
    dates = pd.Series(pd.to_datetime(df["date"].dropna().unique())).sort_values()
    usable_dates = dates.iloc[:-1] if reserve_latest else dates
    if usable_dates.empty:
        return []
    window = usable_dates.iloc[-(trading_days + 1) :]
    selected_dates = list(window.iloc[::interval_days])
    terminal_date = window.iloc[-1]
    if terminal_date not in selected_dates:
        selected_dates.append(terminal_date)
    return sorted(selected_dates)


def filter_stock_pool(
    df: pd.DataFrame,
    min_listing_days: int = config.MIN_LISTING_DAYS,
    min_amount_ma20: float = config.MIN_AMOUNT_MA20,
    max_vol_quantile: float | None = config.MAX_VOL_QUANTILE,
    min_trend_120: float | None = config.MIN_TREND_120,
    min_drawdown_120: float | None = config.MIN_DRAWDOWN_120,
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
    if min_trend_120 is not None and "trend_120" in result.columns:
        mask &= result["trend_120"].notna() & (result["trend_120"] > min_trend_120)
    if min_drawdown_120 is not None and "drawdown_120" in result.columns:
        mask &= result["drawdown_120"].notna() & (result["drawdown_120"] > min_drawdown_120)
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


def _apply_position_weights(
    selected: pd.DataFrame,
    method: str,
    rank_weight_blend: float,
) -> pd.DataFrame:
    """Assign equal, rank-tilted, or inverse-volatility portfolio weights."""
    result = selected.copy()
    if result.empty:
        return result
    if method == "equal":
        result["weight"] = 1 / len(result)
        return result
    if method == "rank_tilt":
        selection_rank = result["score"].rank(method="first", ascending=False)
        rank_weight = (len(result) + 1 - selection_rank) / (len(result) * (len(result) + 1) / 2)
        equal_weight = pd.Series(1 / len(result), index=result.index)
        result["weight"] = (1 - rank_weight_blend) * equal_weight + rank_weight_blend * rank_weight
        return result
    if method == "inverse_volatility":
        if "vol_60" not in result.columns:
            raise ValueError("inverse_volatility weights require vol_60")
        volatility = result["vol_60"].abs().clip(lower=1e-6)
        inverse_volatility = 1 / volatility
        result["weight"] = inverse_volatility / inverse_volatility.sum()
        return result
    raise ValueError(f"unsupported position weight method: {method}")


def _build_controlled_selection(
    pool: pd.DataFrame,
    previous_codes: list[str],
    holding_ages: dict[str, int],
    top_n: int,
    rank_buffer: int,
    max_replacements: int,
    min_holding_days: int,
    min_score_improvement: float | None,
    position_weight_method: str,
    rank_weight_blend: float,
) -> tuple[pd.DataFrame, list[str], dict[str, int]]:
    """Apply holding, rank, and score-difference controls to one rebalance."""
    ranked = pool.sort_values(["score", "code"], ascending=[False, True]).copy()
    ranked["code"] = ranked["code"].astype(str)
    rank_by_code = {code: rank for rank, code in enumerate(ranked["code"], start=1)}
    score_by_code = ranked.set_index("code")["score"].to_dict()
    available = set(rank_by_code)
    previous_codes = [code for code in previous_codes if code in available]
    previous_set = set(previous_codes)

    if not previous_codes:
        selected = ranked["code"].head(top_n).tolist()
    else:
        # Keep the current portfolio by default.  A new name can only displace a
        # mature holding that falls outside the rank buffer and clears the score
        # improvement threshold.
        selected = list(previous_codes)
        removable = [
            code
            for code in selected
            if holding_ages.get(code, 0) >= min_holding_days and rank_by_code[code] > top_n + rank_buffer
        ]
        removable.sort(key=lambda code: (score_by_code[code], code))
        new_candidates = [code for code in ranked["code"] if code not in previous_set]
        replacements = 0
        for candidate in new_candidates:
            if replacements >= max_replacements or not removable:
                break
            incumbent = removable[0]
            if (
                min_score_improvement is not None
                and score_by_code[candidate] < score_by_code[incumbent] + min_score_improvement
            ):
                break
            selected[selected.index(incumbent)] = candidate
            removable.pop(0)
            replacements += 1

        # A name removed from the tradable pool must be replaced even if it has
        # not reached its minimum holding period.
        for candidate in ranked["code"]:
            if len(selected) >= top_n:
                break
            if candidate not in selected:
                selected.append(candidate)

    selected = list(dict.fromkeys(selected))[:top_n]
    selected_frame = ranked.set_index("code", drop=False).loc[selected].copy()
    selected_frame["rank"] = selected_frame["code"].map(rank_by_code).astype(int)
    next_holding_ages = {
        code: holding_ages.get(code, 0) if code in previous_set else 1 for code in selected
    }
    selected_frame["holding_days"] = selected_frame["code"].map(next_holding_ages).astype(int)
    selected_frame = _apply_position_weights(selected_frame, position_weight_method, rank_weight_blend)
    return selected_frame, selected, next_holding_ages


def _build_signal_for_dates(
    df: pd.DataFrame,
    rebalance_dates: list[pd.Timestamp],
    top_n: int = config.TOP_N,
    rank_buffer: int = config.HOLDING_RANK_BUFFER,
    max_replacements: int = config.MAX_REPLACEMENTS_PER_REBALANCE,
    min_holding_days: int = config.MIN_HOLDING_DAYS,
    min_score_improvement: float | None = config.MIN_SCORE_IMPROVEMENT,
    position_weight_method: str = config.POSITION_WEIGHT_METHOD,
    rank_weight_blend: float = config.RANK_WEIGHT_BLEND,
    pool_filter_overrides: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Build a controlled signal using an already selected rebalance calendar."""
    all_dates = pd.Series(pd.to_datetime(df["date"].dropna().unique())).sort_values().tolist()
    date_positions = {date: index for index, date in enumerate(all_dates)}
    frames: list[pd.DataFrame] = []
    previous_codes: list[str] = []
    holding_ages: dict[str, int] = {}
    previous_date: pd.Timestamp | None = None
    for date in rebalance_dates:
        if previous_date is not None:
            elapsed_days = max(1, date_positions[date] - date_positions[previous_date])
            holding_ages = {code: age + elapsed_days for code, age in holding_ages.items()}
        daily = df[df["date"] == date]
        pool = filter_stock_pool(daily, **(pool_filter_overrides or {}))
        if pool.empty:
            continue
        selected, previous_codes, holding_ages = _build_controlled_selection(
            pool,
            previous_codes=previous_codes,
            holding_ages=holding_ages,
            top_n=top_n,
            rank_buffer=rank_buffer,
            max_replacements=max_replacements,
            min_holding_days=min_holding_days,
            min_score_improvement=min_score_improvement,
            position_weight_method=position_weight_method,
            rank_weight_blend=rank_weight_blend,
        )
        selected["date"] = date
        frames.append(selected[["date", "code", "name", "score", "rank", "holding_days", "weight"]])
        previous_date = date
    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "score", "rank", "holding_days", "weight"])
    return pd.concat(frames, ignore_index=True).sort_values(["date", "rank"]).reset_index(drop=True)


def build_frequency_signal(
    df: pd.DataFrame,
    frequency: str,
    top_n: int = config.TOP_N,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    rank_buffer: int = config.HOLDING_RANK_BUFFER,
    max_replacements: int = config.MAX_REPLACEMENTS_PER_REBALANCE,
    min_holding_days: int = config.MIN_HOLDING_DAYS,
    min_score_improvement: float | None = config.MIN_SCORE_IMPROVEMENT,
    position_weight_method: str = config.POSITION_WEIGHT_METHOD,
    rank_weight_blend: float = config.RANK_WEIGHT_BLEND,
    reserve_latest: bool = True,
    pool_filter_overrides: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Build a recent controlled signal for any calendar-based frequency."""
    rebalance_dates = get_frequency_rebalance_dates(
        df,
        frequency=frequency,
        trading_days=trading_days,
        reserve_latest=reserve_latest,
    )
    return _build_signal_for_dates(
        df,
        rebalance_dates=rebalance_dates,
        top_n=top_n,
        rank_buffer=rank_buffer,
        max_replacements=max_replacements,
        min_holding_days=min_holding_days,
        min_score_improvement=min_score_improvement,
        position_weight_method=position_weight_method,
        rank_weight_blend=rank_weight_blend,
        pool_filter_overrides=pool_filter_overrides,
    )


def build_interval_signal(
    df: pd.DataFrame,
    interval_days: int,
    top_n: int = config.TOP_N,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    rank_buffer: int = config.HOLDING_RANK_BUFFER,
    max_replacements: int = config.MAX_REPLACEMENTS_PER_REBALANCE,
    min_holding_days: int = config.MIN_HOLDING_DAYS,
    min_score_improvement: float | None = config.MIN_SCORE_IMPROVEMENT,
    position_weight_method: str = config.POSITION_WEIGHT_METHOD,
    rank_weight_blend: float = config.RANK_WEIGHT_BLEND,
    reserve_latest: bool = True,
    pool_filter_overrides: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Build a controlled signal rebalanced every N trading days."""
    rebalance_dates = get_interval_rebalance_dates(
        df,
        interval_days=interval_days,
        trading_days=trading_days,
        reserve_latest=reserve_latest,
    )
    return _build_signal_for_dates(
        df,
        rebalance_dates=rebalance_dates,
        top_n=top_n,
        rank_buffer=rank_buffer,
        max_replacements=max_replacements,
        min_holding_days=min_holding_days,
        min_score_improvement=min_score_improvement,
        position_weight_method=position_weight_method,
        rank_weight_blend=rank_weight_blend,
        pool_filter_overrides=pool_filter_overrides,
    )


def build_daily_signal(
    df: pd.DataFrame,
    top_n: int = config.TOP_N,
    trading_days: int = config.BACKTEST_TRADING_DAYS,
    rank_buffer: int = config.HOLDING_RANK_BUFFER,
    max_replacements: int = config.MAX_REPLACEMENTS_PER_REBALANCE,
    min_holding_days: int = config.MIN_HOLDING_DAYS,
    min_score_improvement: float | None = config.MIN_SCORE_IMPROVEMENT,
    position_weight_method: str = config.POSITION_WEIGHT_METHOD,
    rank_weight_blend: float = config.RANK_WEIGHT_BLEND,
    reserve_latest: bool = True,
    pool_filter_overrides: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Build a recent daily signal with rank buffering and bounded turnover."""
    return build_frequency_signal(
        df,
        frequency="daily",
        top_n=top_n,
        trading_days=trading_days,
        rank_buffer=rank_buffer,
        max_replacements=max_replacements,
        min_holding_days=min_holding_days,
        min_score_improvement=min_score_improvement,
        position_weight_method=position_weight_method,
        rank_weight_blend=rank_weight_blend,
        reserve_latest=reserve_latest,
        pool_filter_overrides=pool_filter_overrides,
    )


def build_rebalance_signal(df: pd.DataFrame, top_n: int = config.TOP_N) -> pd.DataFrame:
    """Build the configured strategy signal without changing factor history."""
    return build_frequency_signal(df, frequency=config.REBALANCE_FREQUENCY, top_n=top_n)
