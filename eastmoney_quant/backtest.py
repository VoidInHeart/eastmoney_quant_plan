from __future__ import annotations

import pandas as pd

from . import config
from .report import calc_performance


def prepare_daily_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare stock-level daily returns used by the portfolio simulation."""
    result = df.sort_values(["code", "date"]).copy()
    if "ret" not in result.columns:
        result["ret"] = result.groupby("code")["close"].pct_change()
    return result[["date", "code", "ret"]].dropna(subset=["ret"])


def calculate_turnover(prev_position: dict[str, float], new_position: dict[str, float]) -> float:
    """One-way turnover needed to move from previous weights to new weights."""
    codes = set(prev_position) | set(new_position)
    return sum(abs(new_position.get(code, 0.0) - prev_position.get(code, 0.0)) for code in codes) / 2


def _target_position(signal: pd.DataFrame, signal_date: pd.Timestamp) -> dict[str, float]:
    daily = signal[signal["date"] == signal_date]
    return dict(zip(daily["code"].astype(str), daily["weight"].astype(float)))


def expand_positions(signal: pd.DataFrame, trade_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Expand T signals into T+1 daily holdings."""
    if signal.empty or not trade_dates:
        return pd.DataFrame(columns=["date", "code", "weight"])

    trade_dates = sorted(pd.to_datetime(trade_dates))
    signal_dates = sorted(pd.to_datetime(signal["date"].unique()))
    records: list[dict[str, object]] = []
    active_position: dict[str, float] = {}
    execution_plan: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for signal_date in signal_dates:
        execution_dates = [d for d in trade_dates if d > signal_date]
        if execution_dates:
            execution_plan.append((execution_dates[0], signal_date))
    next_execution_index = 0

    for date in trade_dates:
        while next_execution_index < len(execution_plan) and date >= execution_plan[next_execution_index][0]:
            _, signal_date = execution_plan[next_execution_index]
            active_position = _target_position(signal, signal_date)
            next_execution_index += 1
        for code, weight in active_position.items():
            records.append({"date": date, "code": code, "weight": weight})

    return pd.DataFrame(records)


def run_backtest(
    daily_data: pd.DataFrame,
    signal: pd.DataFrame,
    initial_cash: float = config.INITIAL_CASH,
    commission_rate: float = config.COMMISSION_RATE,
) -> dict[str, pd.DataFrame | dict[str, float]]:
    """Run a monthly rebalanced equal-weight backtest without look-ahead."""
    if signal.empty:
        raise ValueError("signal is empty; cannot run backtest")

    returns = prepare_daily_returns(daily_data)
    trade_dates = sorted(pd.to_datetime(daily_data["date"].dropna().unique()))
    signal = signal.copy()
    signal["date"] = pd.to_datetime(signal["date"])
    positions = expand_positions(signal, trade_dates)
    if positions.empty:
        raise ValueError("positions are empty after expanding signals")

    merged = positions.merge(returns, on=["date", "code"], how="left")
    merged["ret"] = merged["ret"].fillna(0.0)
    merged["weighted_ret"] = merged["weight"] * merged["ret"]
    portfolio_ret = merged.groupby("date")["weighted_ret"].sum()

    prev: dict[str, float] = {}
    trades: list[dict[str, object]] = []
    cost_by_date: dict[pd.Timestamp, float] = {}
    for signal_date in sorted(signal["date"].unique()):
        execution_dates = [d for d in trade_dates if d > signal_date]
        if not execution_dates:
            continue
        execution_date = execution_dates[0]
        new = _target_position(signal, signal_date)
        turnover = calculate_turnover(prev, new)
        cost = turnover * commission_rate
        cost_by_date[execution_date] = cost_by_date.get(execution_date, 0.0) + cost
        trades.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "turnover": turnover,
                "cost": cost,
                "holdings": len(new),
            }
        )
        prev = new

    nav_records: list[dict[str, float | pd.Timestamp]] = []
    nav = 1.0
    for date, ret in portfolio_ret.sort_index().items():
        cost = cost_by_date.get(date, 0.0)
        net_ret = ret - cost
        nav = nav * (1 + net_ret)
        nav_records.append(
            {
                "date": date,
                "portfolio_ret": ret,
                "cost": cost,
                "net_ret": net_ret,
                "nav": nav,
                "equity": nav * initial_cash,
            }
        )

    nav_df = pd.DataFrame(nav_records)
    trades_df = pd.DataFrame(trades)
    performance = calc_performance(nav_df, trades_df)
    return {"nav": nav_df, "trades": trades_df, "performance": performance}
