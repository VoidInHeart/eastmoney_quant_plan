from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .report import calc_performance, compare_performance, flatten_performance


def prepare_daily_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Keep close-to-close returns available for research consumers."""
    result = df.sort_values(["code", "date"]).copy()
    if "ret" not in result.columns:
        result["ret"] = result.groupby("code")["close"].pct_change()
    return result[["date", "code", "ret"]].dropna(subset=["ret"])


def calculate_turnover(prev_position: dict[str, float], new_position: dict[str, float]) -> float:
    """Return one-way turnover, treating initial investment as 100% turnover."""
    codes = set(prev_position) | set(new_position)
    buy_turnover = sum(max(new_position.get(code, 0.0) - prev_position.get(code, 0.0), 0.0) for code in codes)
    sell_turnover = sum(max(prev_position.get(code, 0.0) - new_position.get(code, 0.0), 0.0) for code in codes)
    return max(buy_turnover, sell_turnover)


def calculate_transaction_costs(
    prev_position: dict[str, float],
    new_position: dict[str, float],
    commission_rate: float = config.COMMISSION_RATE,
    stamp_tax_rate: float = config.STAMP_TAX_RATE,
    slippage_rate: float = config.SLIPPAGE_RATE,
) -> dict[str, float]:
    """Calculate buy/sell turnover and all simulated trading costs."""
    codes = set(prev_position) | set(new_position)
    buy_turnover = sum(max(new_position.get(code, 0.0) - prev_position.get(code, 0.0), 0.0) for code in codes)
    sell_turnover = sum(max(prev_position.get(code, 0.0) - new_position.get(code, 0.0), 0.0) for code in codes)
    commission_cost = (buy_turnover + sell_turnover) * commission_rate
    stamp_tax_cost = sell_turnover * stamp_tax_rate
    slippage_cost = (buy_turnover + sell_turnover) * slippage_rate
    return {
        "buy_turnover": float(buy_turnover),
        "sell_turnover": float(sell_turnover),
        "turnover": float(max(buy_turnover, sell_turnover)),
        "commission_cost": float(commission_cost),
        "stamp_tax_cost": float(stamp_tax_cost),
        "slippage_cost": float(slippage_cost),
        "cost": float(commission_cost + stamp_tax_cost + slippage_cost),
    }


def _build_execution_plan(signal: pd.DataFrame, trade_dates: list[pd.Timestamp]) -> list[dict[str, object]]:
    """Map close-time signals to the next available opening without look-ahead."""
    plan: list[dict[str, object]] = []
    for signal_date, target in signal.groupby("date", sort=True):
        execution_dates = [date for date in trade_dates if date > signal_date]
        if execution_dates:
            plan.append(
                {
                    "signal_date": pd.Timestamp(signal_date),
                    "execution_date": execution_dates[0],
                    "target": target.copy(),
                }
            )
    return sorted(plan, key=lambda item: item["execution_date"])


def _target_position(target: pd.DataFrame) -> dict[str, float]:
    return dict(zip(target["code"].astype(str), target["weight"].astype(float)))


def _open_return(
    open_prices: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    position: dict[str, float],
) -> tuple[float, int]:
    codes = list(position)
    start = open_prices.loc[start_date].reindex(codes)
    end = open_prices.loc[end_date].reindex(codes)
    valid = start.notna() & end.notna() & (start > 0) & (end > 0)
    stock_returns = (end[valid] / start[valid] - 1).astype(float)
    weights = pd.Series(position, dtype=float).reindex(stock_returns.index)
    # Missing prices are held at their last valuation for this simplified model.
    return float((stock_returns * weights).sum()), int(valid.sum())


def _market_open_return(open_prices: pd.DataFrame, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float:
    start = open_prices.loc[start_date]
    end = open_prices.loc[end_date]
    returns = (end / start - 1).replace([np.inf, -np.inf], np.nan).dropna()
    return float(returns.mean()) if not returns.empty else 0.0


def _infer_annualization_periods(execution_dates: list[pd.Timestamp]) -> int:
    if len(execution_dates) < 2:
        return 252
    spacing = pd.Series(execution_dates).sort_values().diff().dropna().dt.days.median()
    if pd.isna(spacing) or spacing <= 4:
        return 252
    return max(1, round(365.25 / spacing))


def expand_positions(signal: pd.DataFrame, trade_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Expose signal-to-next-open positions for compatibility with research code."""
    if signal.empty or not trade_dates:
        return pd.DataFrame(columns=["date", "code", "weight"])
    plan = _build_execution_plan(signal, sorted(pd.to_datetime(trade_dates)))
    records: list[dict[str, object]] = []
    for item in plan:
        for code, weight in _target_position(item["target"]).items():
            records.append({"date": item["execution_date"], "code": code, "weight": weight})
    return pd.DataFrame(records)


def build_equal_weight_benchmark(
    open_prices: pd.DataFrame,
    periods: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> pd.DataFrame:
    """Build a matching open-to-open all-stock equal-weight benchmark."""
    nav = 1.0
    records: list[dict[str, object]] = []
    for start_date, end_date in periods:
        benchmark_ret = _market_open_return(open_prices, start_date, end_date)
        nav *= 1 + benchmark_ret
        records.append({"date": end_date, "benchmark_ret": benchmark_ret, "benchmark_nav": nav})
    return pd.DataFrame(records)


def run_backtest(
    daily_data: pd.DataFrame,
    signal: pd.DataFrame,
    initial_cash: float = config.INITIAL_CASH,
    commission_rate: float = config.COMMISSION_RATE,
    stamp_tax_rate: float = config.STAMP_TAX_RATE,
    slippage_rate: float = config.SLIPPAGE_RATE,
    annualization_periods: float | None = None,
) -> dict[str, pd.DataFrame | dict[str, float]]:
    """Simulate T+1-open execution and open-to-open holding returns.

    Signals use only the close on day T, trade on the open of T+1, and earn the
    return until the following rebalance opening.  This excludes the unavailable
    T-close to T+1-open gap from a newly generated signal.
    """
    if signal.empty:
        raise ValueError("signal is empty; cannot run backtest")
    required = {"date", "code", "open"}
    missing = required - set(daily_data.columns)
    if missing:
        raise ValueError(f"daily_data missing columns required for open execution: {sorted(missing)}")

    all_dates = pd.to_datetime(daily_data["date"], errors="coerce")
    trade_dates = sorted(pd.Series(all_dates).dropna().unique())

    prepared_signal = signal.copy()
    prepared_signal["date"] = pd.to_datetime(prepared_signal["date"])
    prepared_signal["code"] = prepared_signal["code"].astype(str)
    plan = _build_execution_plan(prepared_signal, trade_dates)
    if len(plan) < 2:
        raise ValueError("at least two executable signals are required for a completed holding period")

    execution_dates = [item["execution_date"] for item in plan]
    prices = daily_data.loc[all_dates.isin(execution_dates), ["date", "code", "open"]].copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices["code"] = prices["code"].astype(str)
    prices["open"] = pd.to_numeric(prices["open"], errors="coerce")
    prices = prices.dropna(subset=["date", "code", "open"])
    open_prices = prices.pivot_table(
        index="date", columns="code", values="open", aggfunc="last"
    )

    nav = 1.0
    previous_position: dict[str, float] = {}
    nav_records: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    periods: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    for current, following in zip(plan, plan[1:]):
        execution_date = current["execution_date"]
        next_execution_date = following["execution_date"]
        position = _target_position(current["target"])
        cost_detail = calculate_transaction_costs(
            previous_position,
            position,
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage_rate=slippage_rate,
        )
        gross_ret, covered_holdings = _open_return(open_prices, execution_date, next_execution_date, position)
        net_ret = gross_ret - cost_detail["cost"]
        nav *= 1 + net_ret
        nav_records.append(
            {
                "date": next_execution_date,
                "signal_date": current["signal_date"],
                "execution_date": execution_date,
                "gross_ret": gross_ret,
                "cost": cost_detail["cost"],
                "net_ret": net_ret,
                "nav": nav,
                "equity": nav * initial_cash,
                "covered_holdings": covered_holdings,
            }
        )
        trades.append(
            {
                "signal_date": current["signal_date"],
                "execution_date": execution_date,
                "next_execution_date": next_execution_date,
                "holdings": len(position),
                **cost_detail,
            }
        )
        periods.append((execution_date, next_execution_date))
        previous_position = position

    nav_df = pd.DataFrame(nav_records)
    trades_df = pd.DataFrame(trades)
    benchmark_df = build_equal_weight_benchmark(open_prices, periods)
    inferred_annualization = _infer_annualization_periods(execution_dates)
    annualization_periods = annualization_periods or inferred_annualization
    strategy_perf = calc_performance(nav_df, trades_df, annualization_periods=annualization_periods)
    benchmark_perf = calc_performance(
        benchmark_df,
        nav_col="benchmark_nav",
        ret_col="benchmark_ret",
        annualization_periods=annualization_periods,
    )
    comparison = compare_performance(strategy_perf, benchmark_perf, nav_df, benchmark_df)
    performance = flatten_performance(strategy_perf, benchmark_perf, comparison)
    performance.update(
        {
            "annualization_periods": annualization_periods,
            "total_cost": float(trades_df["cost"].sum()),
            "total_commission_cost": float(trades_df["commission_cost"].sum()),
            "total_stamp_tax_cost": float(trades_df["stamp_tax_cost"].sum()),
            "total_slippage_cost": float(trades_df["slippage_cost"].sum()),
        }
    )
    return {"nav": nav_df, "benchmark": benchmark_df, "trades": trades_df, "performance": performance}
