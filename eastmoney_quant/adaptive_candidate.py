"""Research-only adaptive small-portfolio candidate.

The candidate deliberately separates *daily monitoring* from normal trading:
scores and risk states are computed after every close, while regular portfolio
changes are made only once per week.  A daily action can only reduce an
existing position after a persistent, combined price-and-rank deterioration;
it never averages down.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config
from .backtest import calculate_transaction_costs
from .report import calc_performance, compare_performance, flatten_performance
from .signal import _build_controlled_selection, filter_stock_pool


@dataclass(frozen=True)
class AdaptiveCandidateConfig:
    """Pre-specified guardrails for the 5--10-name research candidate."""

    strong_breadth: float = 0.60
    neutral_breadth: float = 0.45
    strong_holdings: int = 10
    neutral_holdings: int = 8
    defensive_holdings: int = 5
    strong_exposure: float = 1.00
    neutral_exposure: float = 0.75
    defensive_exposure: float = 0.50
    max_position_weight: float = 0.125
    entry_fraction: float = 0.50
    drawdown_window: int = 20
    drawdown_trigger: float = -0.08
    risk_rank_buffer: int = 20
    risk_days_required: int = 3
    risk_reduction_fraction: float = 0.50
    rank_buffer: int = config.HOLDING_RANK_BUFFER
    max_replacements: int = config.MAX_REPLACEMENTS_PER_REBALANCE
    min_holding_days: int = config.MIN_HOLDING_DAYS
    min_score_improvement: float | None = config.MIN_SCORE_IMPROVEMENT

    def __post_init__(self) -> None:
        if not 0 <= self.neutral_breadth <= self.strong_breadth <= 1:
            raise ValueError("breadth thresholds must satisfy 0 <= neutral <= strong <= 1")
        if not 1 <= self.defensive_holdings <= self.neutral_holdings <= self.strong_holdings <= 10:
            raise ValueError("candidate holdings must be ordered and capped at 10")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1]")
        if not 0 < self.entry_fraction <= 1 or not 0 < self.risk_reduction_fraction <= 1:
            raise ValueError("entry and reduction fractions must be in (0, 1]")
        if self.risk_days_required < 1 or self.drawdown_window < 2:
            raise ValueError("risk_days_required and drawdown_window are too small")


@dataclass(frozen=True)
class PortfolioRegime:
    name: str
    holdings: int
    exposure: float


SCHEDULE_COLUMNS = [
    "signal_date",
    "execution_date",
    "reason",
    "code",
    "name",
    "score",
    "rank",
    "holding_days",
    "weight",
    "breadth",
    "regime",
    "target_holdings",
    "target_exposure",
]


def regime_for_breadth(breadth: float, candidate: AdaptiveCandidateConfig) -> PortfolioRegime:
    """Map observable market breadth to a maximum of 5, 8, or 10 names."""
    if breadth >= candidate.strong_breadth:
        return PortfolioRegime("strong", candidate.strong_holdings, candidate.strong_exposure)
    if breadth >= candidate.neutral_breadth:
        return PortfolioRegime("neutral", candidate.neutral_holdings, candidate.neutral_exposure)
    return PortfolioRegime("defensive", candidate.defensive_holdings, candidate.defensive_exposure)


def apply_weight_guardrails(
    selected: pd.DataFrame,
    exposure: float,
    max_position_weight: float,
    new_codes: set[str] | None = None,
    entry_fraction: float = 1.0,
) -> pd.Series:
    """Scale a selection into cash-aware, capped target weights.

    New names can be entered at a partial weight.  We intentionally do not
    renormalise afterwards: the remainder is explicit cash, rather than hidden
    leverage in the surviving names.
    """
    if selected.empty:
        return pd.Series(dtype=float)
    weights = pd.to_numeric(selected["weight"], errors="coerce").fillna(0.0) * exposure
    if new_codes:
        is_new = selected["code"].astype(str).isin(new_codes)
        weights = weights.where(~is_new, weights * entry_fraction)
    return weights.clip(lower=0.0, upper=max_position_weight)


def _prepare_factors(factors: pd.DataFrame, candidate: AdaptiveCandidateConfig) -> pd.DataFrame:
    required = {"date", "code", "name", "open", "close", "score", "trend_120", "amount_ma20", "listing_days"}
    missing = required - set(factors.columns)
    if missing:
        raise ValueError(f"factors are missing required columns: {sorted(missing)}")
    result = factors.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["date", "code"]).sort_values(["code", "date"]).reset_index(drop=True)
    rolling_peak = result.groupby("code")["close"].transform(
        lambda values: values.rolling(candidate.drawdown_window, min_periods=candidate.drawdown_window).max()
    )
    result["candidate_drawdown"] = result["close"] / rolling_peak - 1
    return result.sort_values(["date", "code"]).reset_index(drop=True)


def _breadth(daily: pd.DataFrame) -> float:
    trend = pd.to_numeric(daily["trend_120"], errors="coerce")
    return float((trend > 0).mean()) if trend.notna().any() else 0.0


def _ranked_pool(daily: pd.DataFrame) -> pd.DataFrame:
    pool = filter_stock_pool(daily)
    if pool.empty:
        return pool
    pool = pool.sort_values(["score", "code"], ascending=[False, True]).copy()
    pool["candidate_rank"] = range(1, len(pool) + 1)
    return pool


def _weekly_signal_dates(dates: list[pd.Timestamp], start_date: pd.Timestamp, end_date: pd.Timestamp) -> set[pd.Timestamp]:
    """Use the final local trading day in every completed Friday-based week."""
    window = pd.Series([date for date in dates if start_date < date < end_date], dtype="datetime64[ns]")
    if window.empty:
        return set()
    weekly = window.groupby(window.dt.to_period("W-FRI")).max()
    return {pd.Timestamp(date) for date in weekly.tolist()}


def _schedule_rows(
    signal_date: pd.Timestamp,
    execution_date: pd.Timestamp,
    reason: str,
    position: dict[str, float],
    metadata: dict[str, dict[str, object]],
    breadth: float,
    regime: PortfolioRegime,
) -> list[dict[str, object]]:
    return [
        {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "reason": reason,
            "code": code,
            "name": metadata.get(code, {}).get("name", ""),
            "score": metadata.get(code, {}).get("score", float("nan")),
            "rank": metadata.get(code, {}).get("rank", float("nan")),
            "holding_days": metadata.get(code, {}).get("holding_days", float("nan")),
            "weight": weight,
            "breadth": breadth,
            "regime": regime.name,
            "target_holdings": len(position),
            "target_exposure": float(sum(position.values())),
        }
        for code, weight in position.items()
    ]


def build_adaptive_schedule(
    factors: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    candidate: AdaptiveCandidateConfig | None = None,
) -> pd.DataFrame:
    """Build close-time targets for the candidate without using future prices.

    Weekly targets may add or replace positions.  Between those targets, the
    only permitted action is a one-time 50% reduction after an existing holding
    has both a persistent 20-day decline and a persistently weak factor rank.
    """
    candidate = candidate or AdaptiveCandidateConfig()
    data = _prepare_factors(factors, candidate)
    dates = sorted(pd.to_datetime(data["date"].unique()))
    start_date = pd.Timestamp(start_date).normalize()
    end_date = pd.Timestamp(end_date).normalize()
    if start_date not in dates or end_date not in dates:
        raise ValueError("start_date and end_date must both be present in the factor data")
    if end_date <= start_date:
        raise ValueError("end_date must be later than start_date")

    date_index = {date: index for index, date in enumerate(dates)}
    weekly_dates = _weekly_signal_dates(dates, start_date, end_date)
    daily_groups = {pd.Timestamp(date): group.copy() for date, group in data.groupby("date", sort=False)}
    planned_position: dict[str, float] = {}
    metadata: dict[str, dict[str, object]] = {}
    holding_ages: dict[str, int] = {}
    risk_streak: dict[str, int] = {}
    reduced_codes: set[str] = set()
    schedule: list[dict[str, object]] = []
    previous_signal_date: pd.Timestamp | None = None
    current_regime = PortfolioRegime("defensive", candidate.defensive_holdings, candidate.defensive_exposure)
    current_breadth = 0.0

    for date in [item for item in dates if start_date <= item < end_date]:
        execution_date = dates[date_index[date] + 1]
        daily = daily_groups[date]
        current_breadth = _breadth(daily)
        current_regime = regime_for_breadth(current_breadth, candidate)
        is_initial = date == start_date
        if is_initial or date in weekly_dates:
            pool = _ranked_pool(daily)
            if pool.empty:
                continue
            rank_by_code = dict(zip(pool["code"].astype(str), pool["candidate_rank"].astype(int)))
            previous_codes = [code for code in planned_position if code in rank_by_code]
            if len(previous_codes) > current_regime.holdings:
                previous_codes = sorted(previous_codes, key=lambda code: rank_by_code[code])[: current_regime.holdings]
            if previous_signal_date is not None:
                elapsed = max(1, date_index[date] - date_index[previous_signal_date])
                holding_ages = {code: age + elapsed for code, age in holding_ages.items()}
            selected, selected_codes, holding_ages = _build_controlled_selection(
                pool,
                previous_codes=previous_codes,
                holding_ages=holding_ages,
                top_n=current_regime.holdings,
                rank_buffer=candidate.rank_buffer,
                max_replacements=candidate.max_replacements,
                min_holding_days=candidate.min_holding_days,
                min_score_improvement=candidate.min_score_improvement,
                position_weight_method="equal",
                rank_weight_blend=config.RANK_WEIGHT_BLEND,
            )
            previous_set = set(planned_position)
            new_codes = set(selected_codes) - previous_set
            selected["code"] = selected["code"].astype(str)
            selected["weight"] = apply_weight_guardrails(
                selected,
                exposure=current_regime.exposure,
                max_position_weight=candidate.max_position_weight,
                new_codes=new_codes if not is_initial else None,
                entry_fraction=candidate.entry_fraction,
            )
            planned_position = dict(zip(selected["code"], selected["weight"].astype(float)))
            metadata = {
                row.code: {
                    "name": row.name,
                    "score": float(row.score),
                    "rank": int(row.rank),
                    "holding_days": int(row.holding_days),
                }
                for row in selected.itertuples(index=False)
            }
            risk_streak = {code: 0 for code in planned_position}
            reduced_codes.clear()
            reason = "initial" if is_initial else "weekly_rebalance"
            schedule.extend(
                _schedule_rows(
                    date,
                    execution_date,
                    reason,
                    planned_position,
                    metadata,
                    current_breadth,
                    current_regime,
                )
            )
            previous_signal_date = date
            continue

        if not planned_position:
            continue
        pool = _ranked_pool(daily)
        rank_by_code = dict(zip(pool["code"].astype(str), pool["candidate_rank"].astype(int))) if not pool.empty else {}
        drawdown_by_code = dict(zip(daily["code"].astype(str), daily["candidate_drawdown"]))
        risk_rank_limit = current_regime.holdings + candidate.risk_rank_buffer
        changed = False
        for code in list(planned_position):
            drawdown = pd.to_numeric(pd.Series([drawdown_by_code.get(code)]), errors="coerce").iloc[0]
            rank = rank_by_code.get(code)
            rank_is_weak = rank is None or rank > risk_rank_limit
            risk_condition = pd.notna(drawdown) and drawdown <= candidate.drawdown_trigger and rank_is_weak
            risk_streak[code] = risk_streak.get(code, 0) + 1 if risk_condition else 0
            if risk_streak[code] >= candidate.risk_days_required and code not in reduced_codes:
                planned_position[code] *= candidate.risk_reduction_fraction
                reduced_codes.add(code)
                changed = True
        if changed:
            schedule.extend(
                _schedule_rows(
                    date,
                    execution_date,
                    "daily_risk_reduce",
                    planned_position,
                    metadata,
                    current_breadth,
                    current_regime,
                )
            )

    if not schedule:
        return pd.DataFrame(columns=SCHEDULE_COLUMNS)
    return pd.DataFrame(schedule, columns=SCHEDULE_COLUMNS).sort_values(["execution_date", "code"]).reset_index(drop=True)


def _portfolio_open_return(
    prices: pd.DataFrame,
    previous_date: pd.Timestamp,
    current_date: pd.Timestamp,
    position: dict[str, float],
) -> tuple[float, int]:
    prior = prices.loc[previous_date].reindex(position)
    current = prices.loc[current_date].reindex(position)
    valid = prior.notna() & current.notna() & (prior > 0) & (current > 0)
    returns = current[valid] / prior[valid] - 1
    weights = pd.Series(position, dtype=float).reindex(returns.index)
    return float((returns * weights).sum()), int(valid.sum())


def _benchmark_open_return(prices: pd.DataFrame, previous_date: pd.Timestamp, current_date: pd.Timestamp) -> float:
    returns = (prices.loc[current_date] / prices.loc[previous_date] - 1).replace([float("inf"), float("-inf")], pd.NA)
    return float(returns.dropna().mean()) if returns.notna().any() else 0.0


def run_adaptive_walk_forward(
    factors: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    candidate: AdaptiveCandidateConfig | None = None,
) -> dict[str, object]:
    """Execute the adaptive schedule at next opens using existing cost settings."""
    candidate = candidate or AdaptiveCandidateConfig()
    data = _prepare_factors(factors, candidate)
    schedule = build_adaptive_schedule(data, start_date, end_date, candidate)
    if schedule.empty:
        raise ValueError("candidate produced no executable targets")
    start_date = pd.Timestamp(start_date).normalize()
    end_date = pd.Timestamp(end_date).normalize()
    targets = {pd.Timestamp(date): group.copy() for date, group in schedule.groupby("execution_date", sort=True)}
    first_execution = min(targets)
    prices_data = data[(data["date"] >= first_execution) & (data["date"] <= end_date)][["date", "code", "open"]].copy()
    prices_data["open"] = pd.to_numeric(prices_data["open"], errors="coerce")
    prices = prices_data.pivot_table(index="date", columns="code", values="open", aggfunc="last")
    mark_dates = [date for date in prices.index if first_execution <= date <= end_date]
    nav = 1.0
    benchmark_nav = 1.0
    live_position: dict[str, float] = {}
    previous_open: pd.Timestamp | None = None
    nav_rows: list[dict[str, object]] = []
    benchmark_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for date in mark_dates:
        gross_return = 0.0
        benchmark_return = 0.0
        covered_holdings = 0
        if previous_open is not None:
            gross_return, covered_holdings = _portfolio_open_return(prices, previous_open, date, live_position)
            benchmark_return = _benchmark_open_return(prices, previous_open, date)
        costs = {"buy_turnover": 0.0, "sell_turnover": 0.0, "turnover": 0.0, "commission_cost": 0.0, "stamp_tax_cost": 0.0, "slippage_cost": 0.0, "cost": 0.0}
        reason = ""
        signal_date = pd.NaT
        if date in targets:
            target = targets[date]
            next_position = dict(zip(target["code"].astype(str), target["weight"].astype(float)))
            costs = calculate_transaction_costs(live_position, next_position)
            live_position = next_position
            reason = str(target["reason"].iloc[0])
            signal_date = pd.Timestamp(target["signal_date"].iloc[0])
            trade_rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": date,
                    "reason": reason,
                    "holdings": len(live_position),
                    "exposure": float(sum(live_position.values())),
                    **costs,
                }
            )
        net_return = gross_return - costs["cost"]
        nav *= 1 + net_return
        benchmark_nav *= 1 + benchmark_return
        nav_rows.append(
            {
                "date": date,
                "signal_date": signal_date,
                "reason": reason,
                "gross_ret": gross_return,
                "cost": costs["cost"],
                "net_ret": net_return,
                "nav": nav,
                "equity": nav * config.INITIAL_CASH,
                "exposure": float(sum(live_position.values())),
                "covered_holdings": covered_holdings,
            }
        )
        benchmark_rows.append({"date": date, "benchmark_ret": benchmark_return, "benchmark_nav": benchmark_nav})
        previous_open = date

    nav_frame = pd.DataFrame(nav_rows)
    benchmark_frame = pd.DataFrame(benchmark_rows)
    trades_frame = pd.DataFrame(trade_rows)
    strategy = calc_performance(nav_frame, trades_frame, annualization_periods=252)
    benchmark = calc_performance(
        benchmark_frame,
        nav_col="benchmark_nav",
        ret_col="benchmark_ret",
        annualization_periods=252,
    )
    performance = flatten_performance(strategy, benchmark, compare_performance(strategy, benchmark, nav_frame, benchmark_frame))
    performance.update(
        {
            "start_signal_date": start_date.date().isoformat(),
            "first_execution_date": first_execution.date().isoformat(),
            "end_date": end_date.date().isoformat(),
            "marked_trading_days": int(len(nav_frame)),
            "total_cost": float(trades_frame["cost"].sum()) if not trades_frame.empty else 0.0,
            "normal_rebalance_signals": int(trades_frame["reason"].isin(["initial", "weekly_rebalance"]).sum()),
            "daily_risk_reductions": int(trades_frame["reason"].eq("daily_risk_reduce").sum()),
            "max_position_weight": candidate.max_position_weight,
            "execution_model": "candidate_next_open_with_daily_risk_reduction",
        }
    )
    return {
        "nav": nav_frame,
        "benchmark": benchmark_frame,
        "trades": trades_frame,
        "schedule": schedule,
        "performance": performance,
    }
