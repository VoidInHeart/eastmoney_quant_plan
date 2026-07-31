"""Open-price execution simulator with explicit pending-order handling.

The legacy backtest is a fast target-weight approximation.  This module is the
stricter research engine used to test execution feasibility: a missing opening
price or a cached suspension cannot be filled, orders remain pending for a
limited number of sessions, and costs are charged only on actual fills.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import count

import pandas as pd

from . import config
from .report import calc_performance, compare_performance


@dataclass
class PendingOrder:
    order_id: int
    code: str
    side: str
    remaining_notional: float
    target_weight: float
    created_date: pd.Timestamp
    signal_date: pd.Timestamp
    attempts: int = 0


def _empty_orders_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "order_id",
            "code",
            "side",
            "status",
            "requested_notional",
            "filled_notional",
            "remaining_notional",
            "price",
            "attempts",
            "commission_cost",
            "stamp_tax_cost",
            "slippage_cost",
            "cost",
            "signal_date",
        ]
    )


def _prepare_suspensions(suspension_events: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["code", "suspension_date", "resumption_date"]
    if suspension_events is None or suspension_events.empty:
        return pd.DataFrame(columns=columns)
    result = suspension_events.reindex(columns=columns).copy()
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["suspension_date"] = pd.to_datetime(result["suspension_date"], errors="coerce")
    result["resumption_date"] = pd.to_datetime(result["resumption_date"], errors="coerce")
    return result.dropna(subset=["code", "suspension_date"])


def _is_suspended(code: str, date: pd.Timestamp, suspensions: pd.DataFrame) -> bool:
    if suspensions.empty:
        return False
    matches = suspensions[suspensions["code"] == code]
    return bool(
        (
            (matches["suspension_date"] <= date)
            & (matches["resumption_date"].isna() | (matches["resumption_date"] >= date))
        ).any()
    )


def _execution_plan(signal: pd.DataFrame, trade_dates: list[pd.Timestamp]) -> dict[pd.Timestamp, dict[str, object]]:
    prepared = signal.copy()
    prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce")
    prepared["code"] = prepared["code"].astype(str).str.zfill(6)
    plan: dict[pd.Timestamp, dict[str, object]] = {}
    for signal_date, target in prepared.dropna(subset=["date"]).groupby("date", sort=True):
        execution_date = next((date for date in trade_dates if date > signal_date), None)
        if execution_date is None:
            continue
        weights = target.set_index("code")["weight"].astype(float).to_dict()
        plan[execution_date] = {"signal_date": pd.Timestamp(signal_date), "weights": weights}
    return plan


def run_executable_backtest(
    daily_data: pd.DataFrame,
    signal: pd.DataFrame,
    initial_cash: float = config.INITIAL_CASH,
    commission_rate: float = config.COMMISSION_RATE,
    stamp_tax_rate: float = config.STAMP_TAX_RATE,
    slippage_rate: float = config.SLIPPAGE_RATE,
    suspension_events: pd.DataFrame | None = None,
    max_order_attempts: int = 5,
) -> dict[str, pd.DataFrame | dict[str, float]]:
    """Simulate target-weight orders, cash, shares and failed open executions.

    A pending order is retried at each following opening for at most
    ``max_order_attempts`` sessions.  If another signal arrives first, any
    remaining older orders are explicitly cancelled as superseded.  This is a
    deterministic policy, not a hidden next-valid-price fill.
    """
    required = {"date", "code", "open", "close"}
    missing = required - set(daily_data.columns)
    if missing:
        raise ValueError(f"daily_data missing required columns: {sorted(missing)}")
    if signal.empty:
        raise ValueError("signal is empty; cannot execute orders")
    if max_order_attempts < 1:
        raise ValueError("max_order_attempts must be at least 1")

    prices = daily_data[["date", "code", "open", "close"]].copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["code"] = prices["code"].astype(str).str.zfill(6)
    prices["open"] = pd.to_numeric(prices["open"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["date"]).drop_duplicates(["date", "code"], keep="last")
    trade_dates = sorted(prices["date"].unique())
    plan = _execution_plan(signal, trade_dates)
    if not plan:
        raise ValueError("no T+1 execution date is available for the supplied signal")

    daily_frames = {date: frame.set_index("code") for date, frame in prices.groupby("date", sort=True)}
    suspensions = _prepare_suspensions(suspension_events)
    first_date = min(plan)
    active_dates = [date for date in trade_dates if date >= first_date]
    order_ids = count(1)
    pending: list[PendingOrder] = []
    positions: dict[str, float] = {}
    cash = float(initial_cash)
    last_prices: dict[str, float] = {}
    events: list[dict[str, object]] = []
    nav_records: list[dict[str, object]] = []

    def open_price(frame: pd.DataFrame, code: str) -> tuple[float | None, str | None]:
        if code not in frame.index:
            return None, "missing_open"
        value = frame.at[code, "open"]
        if pd.isna(value) or float(value) <= 0:
            return None, "invalid_open"
        if _is_suspended(code, current_date, suspensions):
            return None, "suspended"
        return float(value), None

    def portfolio_value(frame: pd.DataFrame) -> float:
        value = cash
        for code, shares in positions.items():
            price = None
            if code in frame.index:
                candidate = frame.at[code, "open"]
                if pd.notna(candidate) and float(candidate) > 0:
                    price = float(candidate)
            price = price if price is not None else last_prices.get(code)
            if price is not None:
                value += shares * price
        return float(value)

    def log_event(
        order: PendingOrder,
        status: str,
        requested: float,
        filled: float = 0.0,
        price: float | None = None,
        commission: float = 0.0,
        stamp_tax: float = 0.0,
        slippage: float = 0.0,
    ) -> None:
        events.append(
            {
                "date": current_date,
                "order_id": order.order_id,
                "code": order.code,
                "side": order.side,
                "status": status,
                "requested_notional": float(requested),
                "filled_notional": float(filled),
                "remaining_notional": float(order.remaining_notional),
                "price": price,
                "attempts": order.attempts,
                "commission_cost": float(commission),
                "stamp_tax_cost": float(stamp_tax),
                "slippage_cost": float(slippage),
                "cost": float(commission + stamp_tax + slippage),
                "signal_date": order.signal_date,
            }
        )

    def process_pending(frame: pd.DataFrame) -> None:
        nonlocal cash, pending
        remaining: list[PendingOrder] = []
        # Sales first so a completed sale can fund a queued buy on the same open.
        for order in sorted(pending, key=lambda item: (item.side != "sell", item.order_id)):
            requested = order.remaining_notional
            price, issue = open_price(frame, order.code)
            if issue is not None:
                order.attempts += 1
                status = "cancelled_untradable" if order.attempts >= max_order_attempts else "deferred_untradable"
                log_event(order, status, requested)
                if status.startswith("deferred"):
                    remaining.append(order)
                continue

            if order.side == "sell":
                available = positions.get(order.code, 0.0) * price
                filled = min(requested, available)
                if filled <= 1e-8:
                    log_event(order, "cancelled_no_position", requested, price=price)
                    continue
                positions[order.code] = max(0.0, positions.get(order.code, 0.0) - filled / price)
                if positions[order.code] <= 1e-10:
                    positions.pop(order.code, None)
                commission = filled * commission_rate
                stamp_tax = filled * stamp_tax_rate
                slippage = filled * slippage_rate
                cash += filled - commission - stamp_tax - slippage
            else:
                maximum = cash / (1 + commission_rate + slippage_rate)
                filled = min(requested, maximum)
                if filled <= 1e-8:
                    order.attempts += 1
                    status = "cancelled_insufficient_cash" if order.attempts >= max_order_attempts else "deferred_insufficient_cash"
                    log_event(order, status, requested, price=price)
                    if status.startswith("deferred"):
                        remaining.append(order)
                    continue
                positions[order.code] = positions.get(order.code, 0.0) + filled / price
                commission = filled * commission_rate
                stamp_tax = 0.0
                slippage = filled * slippage_rate
                cash -= filled + commission + slippage

            order.remaining_notional = max(0.0, requested - filled)
            if order.remaining_notional <= 1e-6:
                log_event(order, "filled", requested, filled, price, commission, stamp_tax, slippage)
            else:
                order.attempts += 1
                status = "cancelled_partial" if order.attempts >= max_order_attempts else "partial"
                log_event(order, status, requested, filled, price, commission, stamp_tax, slippage)
                if status == "partial":
                    remaining.append(order)
        pending = remaining

    def create_target_orders(frame: pd.DataFrame, target: dict[str, object]) -> None:
        target_weights = {str(code).zfill(6): float(weight) for code, weight in target["weights"].items()}
        value = portfolio_value(frame)
        all_codes = set(positions) | set(target_weights)
        created: list[PendingOrder] = []
        for code in sorted(all_codes):
            reference = None
            if code in frame.index:
                candidate = frame.at[code, "open"]
                if pd.notna(candidate) and float(candidate) > 0:
                    reference = float(candidate)
            reference = reference if reference is not None else last_prices.get(code)
            current_value = positions.get(code, 0.0) * reference if reference is not None else 0.0
            desired_value = value * target_weights.get(code, 0.0)
            delta = desired_value - current_value
            if abs(delta) <= max(1.0, value * 1e-6):
                continue
            side = "buy" if delta > 0 else "sell"
            # A buy consumes both notional and its commission/slippage.  Reserve
            # those costs when the target order is created so a fully funded
            # initial allocation does not leave a meaningless micro-order.
            requested_notional = abs(delta)
            if side == "buy":
                requested_notional /= 1 + commission_rate + slippage_rate
            created.append(
                PendingOrder(
                    order_id=next(order_ids),
                    code=code,
                    side=side,
                    remaining_notional=requested_notional,
                    target_weight=target_weights.get(code, 0.0),
                    created_date=current_date,
                    signal_date=target["signal_date"],
                )
            )
        pending.extend(created)

    previous_equity = float(initial_cash)
    for current_date in active_dates:
        frame = daily_frames[current_date]
        process_pending(frame)
        if current_date in plan:
            # A newer target supersedes any unresolved earlier intention after it
            # has received its scheduled retry at today's opening.
            for order in pending:
                log_event(order, "cancelled_superseded", order.remaining_notional)
            pending.clear()
            create_target_orders(frame, plan[current_date])
            process_pending(frame)

        close_values = frame["close"].dropna()
        for code, close in close_values.items():
            if float(close) > 0:
                last_prices[str(code).zfill(6)] = float(close)
        equity = cash + sum(shares * last_prices.get(code, 0.0) for code, shares in positions.items())
        nav_records.append(
            {
                "date": current_date,
                "equity": float(equity),
                "nav": float(equity / initial_cash),
                "cash": float(cash),
                "invested_positions": int(len(positions)),
                "net_ret": float(equity / previous_equity - 1) if previous_equity else 0.0,
            }
        )
        previous_equity = float(equity)

    nav = pd.DataFrame(nav_records)
    close_matrix = prices.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(active_dates)
    benchmark_returns = close_matrix.pct_change().mean(axis=1).fillna(0.0)
    benchmark = pd.DataFrame(
        {
            "date": active_dates,
            "benchmark_ret": benchmark_returns.to_numpy(dtype=float),
            "benchmark_nav": (1 + benchmark_returns).cumprod().to_numpy(dtype=float),
        }
    )
    order_events = pd.DataFrame(events) if events else _empty_orders_frame()
    fills = order_events[order_events["filled_notional"] > 0].copy()
    if fills.empty:
        trades = pd.DataFrame(columns=["date", "turnover", "cost"])
    else:
        nav_by_date = nav.set_index("date")["equity"]
        trades = fills.groupby("date", as_index=False).agg(
            filled_notional=("filled_notional", "sum"),
            cost=("cost", "sum"),
            commission_cost=("commission_cost", "sum"),
            stamp_tax_cost=("stamp_tax_cost", "sum"),
            slippage_cost=("slippage_cost", "sum"),
        )
        trades["turnover"] = trades.apply(
            lambda row: row["filled_notional"] / max(float(nav_by_date.get(row["date"], initial_cash)), 1.0), axis=1
        )
    performance = calc_performance(nav, trades, annualization_periods=252)
    benchmark_performance = calc_performance(
        benchmark,
        nav_col="benchmark_nav",
        ret_col="benchmark_ret",
        annualization_periods=252,
    )
    comparison = compare_performance(performance, benchmark_performance, nav, benchmark)
    performance.update(
        {
            "total_cost": float(fills["cost"].sum()) if not fills.empty else 0.0,
            "filled_order_events": int(len(fills)),
            "deferred_order_events": int(order_events["status"].astype(str).str.startswith("deferred").sum()),
            "cancelled_order_events": int(order_events["status"].astype(str).str.startswith("cancelled").sum()),
            "pending_orders_at_end": int(len(pending)),
        }
    )
    performance.update({f"benchmark_{key}": value for key, value in benchmark_performance.items()})
    performance.update(comparison)
    pending_orders = pd.DataFrame([asdict(order) for order in pending])
    return {
        "nav": nav,
        "benchmark": benchmark,
        "trades": trades,
        "order_events": order_events,
        "fills": fills,
        "pending_orders": pending_orders,
        "performance": performance,
    }
