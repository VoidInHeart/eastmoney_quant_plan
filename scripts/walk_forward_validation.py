"""Run a no-look-ahead, post-cache walk-forward validation for the baseline.

The current strategy rebalances weekly.  This script starts from the portfolio
that was knowable at ``start_date`` close, executes it at the next available
open, then marks the portfolio and benchmark at every subsequent open.  Future
dates are never used to create a rebalance target: factor values are all
backward-looking and each new target is built only after its signal-date close.

It intentionally uses the project's default fast-execution assumptions, so it
does not claim to model limit-up/limit-down fills or order-book liquidity.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# `python scripts/walk_forward_validation.py` puts only scripts/ on sys.path.
# Add the repository root so the documented command works on Windows as well.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eastmoney_quant import config
from eastmoney_quant.backtest import calculate_transaction_costs
from eastmoney_quant.prediction import load_factor_data
from eastmoney_quant.report import calc_performance, compare_performance, flatten_performance
from eastmoney_quant.signal import (
    _build_controlled_selection,
    build_frequency_signal,
    filter_stock_pool,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the baseline from a historical cache cut-off")
    parser.add_argument(
        "--start-date",
        required=True,
        help="Close date whose portfolio starts the validation (YYYYMMDD or YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Last available close/open used for marking; defaults to the latest factor date",
    )
    parser.add_argument("--top-n", type=int, default=config.TOP_N, help="Portfolio size")
    parser.add_argument(
        "--refresh-factors",
        action="store_true",
        help="Rebuild factors from the clean cache before validation",
    )
    return parser.parse_args()


def _normalise_factors(factors: pd.DataFrame) -> pd.DataFrame:
    result = factors.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result.dropna(subset=["date", "code"]).sort_values(["date", "code"]).reset_index(drop=True)


def _completed_week_dates(dates: list[pd.Timestamp], start_date: pd.Timestamp, end_date: pd.Timestamp) -> list[pd.Timestamp]:
    """Return finished Friday-based weeks strictly after ``start_date``.

    The final partial week is excluded.  A target from that week would only be
    executable after the evaluation end date and should not affect its return.
    """
    date_series = pd.Series(dates, dtype="datetime64[ns]")
    weekly = date_series.groupby(date_series.dt.to_period("W-FRI")).max()
    completed: list[pd.Timestamp] = []
    for period, signal_date in weekly.items():
        period_end = pd.Timestamp(period.end_time).normalize()
        if start_date < signal_date <= end_date and period_end <= end_date:
            completed.append(pd.Timestamp(signal_date))
    return sorted(completed)


def _selection_at_start(factors: pd.DataFrame, start_date: pd.Timestamp, top_n: int) -> pd.DataFrame:
    """Reconstruct the production portfolio using only rows available at start close."""
    known = factors[factors["date"] <= start_date]
    signal = build_frequency_signal(
        known,
        frequency=config.REBALANCE_FREQUENCY,
        top_n=top_n,
        reserve_latest=False,
    )
    selection = signal[signal["date"] == start_date].copy()
    if selection.empty:
        raise ValueError(f"No baseline portfolio could be constructed at {start_date.date()}")
    if len(selection) != top_n:
        raise ValueError(f"Expected {top_n} holdings at {start_date.date()}, found {len(selection)}")
    return selection.sort_values("rank").reset_index(drop=True)


def _portfolio_open_return(
    prices: pd.DataFrame,
    previous_date: pd.Timestamp,
    current_date: pd.Timestamp,
    position: dict[str, float],
) -> tuple[float, int]:
    prior = prices.loc[previous_date].reindex(position)
    current = prices.loc[current_date].reindex(position)
    valid = prior.notna() & current.notna() & (prior > 0) & (current > 0)
    stock_returns = current[valid] / prior[valid] - 1
    weights = pd.Series(position, dtype=float).reindex(stock_returns.index)
    return float((stock_returns * weights).sum()), int(valid.sum())


def _benchmark_open_return(prices: pd.DataFrame, previous_date: pd.Timestamp, current_date: pd.Timestamp) -> float:
    prior = prices.loc[previous_date]
    current = prices.loc[current_date]
    returns = (current / prior - 1).replace([float("inf"), float("-inf")], pd.NA).dropna()
    return float(returns.mean()) if not returns.empty else 0.0


def _next_trade_date(dates: list[pd.Timestamp], signal_date: pd.Timestamp) -> pd.Timestamp | None:
    for date in dates:
        if date > signal_date:
            return date
    return None


def run_walk_forward(
    factors: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    top_n: int = config.TOP_N,
) -> dict[str, object]:
    """Simulate the baseline sequentially from one already observed close."""
    factors = _normalise_factors(factors)
    all_dates = sorted(pd.to_datetime(factors["date"].unique()))
    if start_date not in all_dates:
        raise ValueError(f"Start date {start_date.date()} is absent from the factor cache")
    if end_date not in all_dates:
        raise ValueError(f"End date {end_date.date()} is absent from the factor cache")
    if end_date <= start_date:
        raise ValueError("end date must be after start date")

    start_selection = _selection_at_start(factors, start_date, top_n)
    position = dict(zip(start_selection["code"], start_selection["weight"].astype(float)))
    holding_ages = dict(zip(start_selection["code"], start_selection["holding_days"].astype(int)))
    last_signal_date = start_date
    date_positions = {date: index for index, date in enumerate(all_dates)}

    targets: dict[pd.Timestamp, tuple[pd.Timestamp, pd.DataFrame]] = {}
    first_execution = _next_trade_date(all_dates, start_date)
    if first_execution is None or first_execution > end_date:
        raise ValueError("No completed open is available after the start signal")
    targets[first_execution] = (start_date, start_selection)

    rebalance_rows: list[pd.DataFrame] = [start_selection.assign(signal_date=start_date, execution_date=first_execution)]
    for signal_date in _completed_week_dates(all_dates, start_date, end_date):
        execution_date = _next_trade_date(all_dates, signal_date)
        if execution_date is None or execution_date > end_date:
            continue
        elapsed_days = date_positions[signal_date] - date_positions[last_signal_date]
        holding_ages = {code: age + max(1, elapsed_days) for code, age in holding_ages.items()}
        pool = filter_stock_pool(factors[factors["date"] == signal_date])
        if pool.empty:
            continue
        selection, selected_codes, holding_ages = _build_controlled_selection(
            pool,
            previous_codes=list(position),
            holding_ages=holding_ages,
            top_n=top_n,
            rank_buffer=config.HOLDING_RANK_BUFFER,
            max_replacements=config.MAX_REPLACEMENTS_PER_REBALANCE,
            min_holding_days=config.MIN_HOLDING_DAYS,
            min_score_improvement=config.MIN_SCORE_IMPROVEMENT,
            position_weight_method=config.POSITION_WEIGHT_METHOD,
            rank_weight_blend=config.RANK_WEIGHT_BLEND,
        )
        position = {code: position.get(code, 0.0) for code in selected_codes}
        # Keep the target in a separate calendar.  ``position`` is only used to
        # seed the next close-time selection; the open-to-open simulation below
        # switches holdings on its actual execution date.
        targets[execution_date] = (signal_date, selection)
        rebalance_rows.append(selection.assign(signal_date=signal_date, execution_date=execution_date))
        last_signal_date = signal_date

    price_rows = factors[(factors["date"] >= first_execution) & (factors["date"] <= end_date)][["date", "code", "open"]].copy()
    price_rows["open"] = pd.to_numeric(price_rows["open"], errors="coerce")
    prices = price_rows.pivot_table(index="date", columns="code", values="open", aggfunc="last")
    mark_dates = [date for date in all_dates if first_execution <= date <= end_date and date in prices.index]

    nav = 1.0
    benchmark_nav = 1.0
    live_position: dict[str, float] = {}
    previous_open: pd.Timestamp | None = None
    nav_records: list[dict[str, object]] = []
    benchmark_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []

    for date in mark_dates:
        gross_ret = 0.0
        benchmark_ret = 0.0
        covered_holdings = 0
        if previous_open is not None:
            gross_ret, covered_holdings = _portfolio_open_return(prices, previous_open, date, live_position)
            benchmark_ret = _benchmark_open_return(prices, previous_open, date)

        cost_detail = {"buy_turnover": 0.0, "sell_turnover": 0.0, "turnover": 0.0, "commission_cost": 0.0, "stamp_tax_cost": 0.0, "slippage_cost": 0.0, "cost": 0.0}
        signal_date = pd.NaT
        if date in targets:
            signal_date, target = targets[date]
            next_position = dict(zip(target["code"], target["weight"].astype(float)))
            cost_detail = calculate_transaction_costs(live_position, next_position)
            live_position = next_position
            trade_records.append(
                {
                    "signal_date": signal_date,
                    "execution_date": date,
                    "holdings": len(live_position),
                    **cost_detail,
                }
            )

        net_ret = gross_ret - cost_detail["cost"]
        nav *= 1 + net_ret
        benchmark_nav *= 1 + benchmark_ret
        nav_records.append(
            {
                "date": date,
                "signal_date": signal_date,
                "gross_ret": gross_ret,
                "cost": cost_detail["cost"],
                "net_ret": net_ret,
                "nav": nav,
                "equity": nav * config.INITIAL_CASH,
                "covered_holdings": covered_holdings,
            }
        )
        benchmark_records.append({"date": date, "benchmark_ret": benchmark_ret, "benchmark_nav": benchmark_nav})
        previous_open = date

    nav_df = pd.DataFrame(nav_records)
    benchmark_df = pd.DataFrame(benchmark_records)
    trades_df = pd.DataFrame(trade_records)
    strategy = calc_performance(nav_df, trades_df, annualization_periods=252)
    benchmark = calc_performance(
        benchmark_df,
        nav_col="benchmark_nav",
        ret_col="benchmark_ret",
        annualization_periods=252,
    )
    performance = flatten_performance(strategy, benchmark, compare_performance(strategy, benchmark, nav_df, benchmark_df))
    performance.update(
        {
            "annualization_periods": 252,
            "total_cost": float(trades_df["cost"].sum()) if not trades_df.empty else 0.0,
            "start_signal_date": start_date.date().isoformat(),
            "first_execution_date": first_execution.date().isoformat(),
            "end_date": end_date.date().isoformat(),
            "marked_trading_days": int(len(nav_df)),
            "new_rebalance_signals": int(max(0, len(trade_records) - 1)),
            "execution_model": "default_fast_open_to_open",
        }
    )
    positions = pd.concat(rebalance_rows, ignore_index=True).sort_values(["signal_date", "rank"])
    return {"nav": nav_df, "benchmark": benchmark_df, "trades": trades_df, "positions": positions, "performance": performance}


def main() -> None:
    args = _parse_args()
    start_date = pd.Timestamp(args.start_date).normalize()
    factors = load_factor_data(refresh=args.refresh_factors)
    factors = _normalise_factors(factors)
    end_date = pd.Timestamp(args.end_date).normalize() if args.end_date else factors["date"].max()
    result = run_walk_forward(factors, start_date=start_date, end_date=end_date, top_n=args.top_n)

    stem = f"walk_forward_{start_date:%Y%m%d}_{end_date:%Y%m%d}"
    config.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "nav": config.RESULT_DIR / f"{stem}_nav.csv",
        "benchmark": config.RESULT_DIR / f"{stem}_benchmark.csv",
        "trades": config.RESULT_DIR / f"{stem}_trades.csv",
        "positions": config.RESULT_DIR / f"{stem}_positions.csv",
        "summary": config.RESULT_DIR / f"{stem}_summary.json",
    }
    result["nav"].to_csv(output_paths["nav"], index=False, encoding="utf-8-sig")
    result["benchmark"].to_csv(output_paths["benchmark"], index=False, encoding="utf-8-sig")
    result["trades"].to_csv(output_paths["trades"], index=False, encoding="utf-8-sig")
    result["positions"].to_csv(output_paths["positions"], index=False, encoding="utf-8-sig")
    with Path(output_paths["summary"]).open("w", encoding="utf-8") as handle:
        json.dump(result["performance"], handle, ensure_ascii=False, indent=2, default=str)

    performance = result["performance"]
    print(f"Validation: {performance['start_signal_date']} close -> {performance['end_date']} open")
    print(f"Strategy return: {performance['cumulative_return']:.2%}")
    print(f"Benchmark return: {performance['benchmark_cumulative_return']:.2%}")
    print(f"Excess return: {performance['excess_cumulative_return']:.2%}")
    print(f"Total cost: {performance['total_cost']:.2%}")
    print(f"Summary: {output_paths['summary']}")


if __name__ == "__main__":
    main()
