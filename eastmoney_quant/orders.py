"""Turn the latest selected stocks into concrete initial-buy order suggestions.

The strategy keeps weights internally for research and performance tracking.
This module deliberately does not expose those weights to the user-facing order
flow: a new, empty 200,000 RMB account is split equally across the selected
stocks and each order is rounded down to an A-share board lot.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from . import config


def _normalise_code(values: pd.Series) -> pd.Series:
    return values.astype(str).str.extract(r"(\d{1,6})", expand=False).str.zfill(6)


def _first_present(columns: pd.Index, choices: tuple[str, ...]) -> str | None:
    return next((column for column in choices if column in columns), None)


def _floor_to_tick(value: float, tick: float) -> float:
    return math.floor((value + 1e-12) / tick) * tick


def normalise_open_prices(open_prices: pd.DataFrame) -> pd.DataFrame:
    """Return validated ``code``/``open_price`` records from a user-supplied table."""
    code_column = _first_present(open_prices.columns, ("code", "证券代码", "股票代码"))
    price_column = _first_present(open_prices.columns, ("open_price", "open", "开盘价", "开盘"))
    if code_column is None or price_column is None:
        raise ValueError("open-price input must contain code and open_price columns")

    result = open_prices[[code_column, price_column]].rename(columns={code_column: "code", price_column: "open_price"}).copy()
    result["code"] = _normalise_code(result["code"])
    result["open_price"] = pd.to_numeric(result["open_price"], errors="coerce")
    result = result.dropna(subset=["code", "open_price"])
    if result.empty or result["open_price"].le(0).any():
        raise ValueError("open prices must be positive numbers")
    if result["code"].duplicated().any():
        duplicates = ", ".join(result.loc[result["code"].duplicated(keep=False), "code"].unique())
        raise ValueError(f"open-price input contains duplicate codes: {duplicates}")
    return result.reset_index(drop=True)


def build_open_price_template(portfolio: pd.DataFrame) -> pd.DataFrame:
    """Build a blank CSV template for the next trading day's opening prices."""
    if portfolio.empty:
        raise ValueError("latest portfolio is empty")
    result = portfolio.copy()
    result["code"] = _normalise_code(result["code"])
    reference_column = _first_present(result.columns, ("reference_close", "previous_close", "close"))
    columns = [column for column in ("signal_date", "execution_date", "rank", "code", "name") if column in result]
    if reference_column is not None:
        result = result.rename(columns={reference_column: "previous_close"})
        columns.append("previous_close")
    result = result[columns].copy()
    result["open_price"] = pd.NA
    return result


def build_buy_recommendations(
    portfolio: pd.DataFrame,
    open_prices: pd.DataFrame,
    total_assets: float = config.ORDER_TOTAL_ASSETS,
    board_lot: int = config.ORDER_BOARD_LOT,
    price_tick: float = config.ORDER_PRICE_TICK,
    open_premium: float = config.ORDER_OPEN_PREMIUM,
    max_gap_from_close: float = config.ORDER_MAX_GAP_FROM_CLOSE,
    commission_rate: float = config.COMMISSION_RATE,
    minimum_commission: float = config.ORDER_MIN_COMMISSION,
) -> pd.DataFrame:
    """Create equal-cash, whole-board-lot buy suggestions from opening prices.

    ``buy_limit_price`` allows a modest premium over the observed open, but is
    capped relative to the previous close to avoid mechanically chasing a large
    opening gap.  The quantity is calculated against that limit, including the
    estimated commission, so every order remains within its equal cash budget.
    """
    if portfolio.empty:
        raise ValueError("latest portfolio is empty")
    if total_assets <= 0 or board_lot < 1 or price_tick <= 0:
        raise ValueError("total_assets, board_lot and price_tick must be positive")
    if open_premium < 0 or max_gap_from_close < 0 or commission_rate < 0 or minimum_commission < 0:
        raise ValueError("order price and commission settings cannot be negative")

    selected = portfolio.copy()
    if "code" not in selected:
        raise ValueError("latest portfolio must contain code")
    selected["code"] = _normalise_code(selected["code"])
    selected = selected.drop_duplicates(subset=["code"], keep="first")
    if len(selected) > config.LIVE_MAX_HOLDINGS:
        raise ValueError(
            f"latest portfolio has {len(selected)} stocks; regenerate it with the 5--10-name live workflow before creating orders"
        )
    prices = normalise_open_prices(open_prices)

    selected_codes = set(selected["code"])
    supplied_codes = set(prices["code"])
    missing = sorted(selected_codes - supplied_codes)
    if missing:
        raise ValueError(f"opening prices are missing for selected codes: {', '.join(missing)}")

    merged = selected.merge(prices, on="code", how="left", validate="one_to_one")
    reference_column = _first_present(merged.columns, ("reference_close", "close", "previous_close"))
    if reference_column is None:
        raise ValueError("latest portfolio must contain a reference close price")
    merged["previous_close"] = pd.to_numeric(merged[reference_column], errors="coerce")
    if merged["previous_close"].isna().any() or merged["previous_close"].le(0).any():
        raise ValueError("latest portfolio contains invalid reference close prices")

    budget_per_stock = float(total_assets) / len(merged)
    records: list[dict[str, object]] = []
    untradable: list[str] = []
    for _, row in merged.sort_values([column for column in ("rank", "code") if column in merged]).iterrows():
        opening_cap = _floor_to_tick(float(row["open_price"]) * (1 + open_premium), price_tick)
        close_cap = _floor_to_tick(float(row["previous_close"]) * (1 + max_gap_from_close), price_tick)
        limit_price = min(opening_cap, close_cap)
        quantity = int(math.floor((budget_per_stock - minimum_commission) / (limit_price * board_lot)) * board_lot)
        while quantity > 0:
            notional = quantity * limit_price
            commission = max(notional * commission_rate, minimum_commission)
            if notional + commission <= budget_per_stock + 1e-9:
                break
            quantity -= board_lot
        if quantity <= 0:
            untradable.append(str(row["code"]))
            continue

        notional = quantity * limit_price
        commission = max(notional * commission_rate, minimum_commission)
        records.append(
            {
                "signal_date": row.get("signal_date"),
                "execution_date": row.get("execution_date"),
                "rank": row.get("rank"),
                "code": row["code"],
                "name": row.get("name", ""),
                "side": "buy",
                "order_type": "limit",
                "previous_close": round(float(row["previous_close"]), 2),
                "open_price": round(float(row["open_price"]), 2),
                "buy_limit_price": round(limit_price, 2),
                "quantity": quantity,
                "target_budget": round(budget_per_stock, 2),
                "estimated_notional": round(notional, 2),
                "estimated_commission": round(commission, 2),
                "estimated_total_cost": round(notional + commission, 2),
                "unused_budget": round(budget_per_stock - notional - commission, 2),
                "price_rule": f"min(open*{1 + open_premium:.3f}, previous_close*{1 + max_gap_from_close:.3f})",
            }
        )
    if untradable:
        raise ValueError(f"budget is insufficient to buy one board lot for: {', '.join(untradable)}")

    return pd.DataFrame(records)


def build_initial_buy_recommendations_from_prescreen(
    prescreen: pd.DataFrame,
    open_prices: pd.DataFrame,
    total_assets: float = config.ORDER_TOTAL_ASSETS,
    open_premium: float = config.ORDER_OPEN_PREMIUM,
    max_gap_from_close: float = config.ORDER_MAX_GAP_FROM_CLOSE,
) -> pd.DataFrame:
    """Select a safe 5--10-name initial portfolio from a 20-name pre-screen.

    All twenty supplied opening prices are checked before the top-ranked names
    are selected.  A stock opening more than the close-gap guardrail is skipped
    rather than being forced into the first-day portfolio.
    """
    if prescreen.empty:
        raise ValueError("daily pre-screen is empty")
    candidates = prescreen.copy()
    if "code" not in candidates or "rank" not in candidates:
        raise ValueError("daily pre-screen must contain code and rank")
    candidates["code"] = _normalise_code(candidates["code"])
    candidates = candidates.drop_duplicates(subset=["code"], keep="first")
    if len(candidates) < config.LIVE_MIN_HOLDINGS:
        raise ValueError("daily pre-screen has fewer than five candidates")

    prices = normalise_open_prices(open_prices)
    missing = sorted(set(candidates["code"]) - set(prices["code"]))
    if missing:
        raise ValueError(f"opening prices are missing for pre-screen codes: {', '.join(missing)}")

    reference_column = _first_present(candidates.columns, ("previous_close", "reference_close", "close"))
    if reference_column is None:
        raise ValueError("daily pre-screen must contain previous_close")
    candidates["previous_close"] = pd.to_numeric(candidates[reference_column], errors="coerce")
    if candidates["previous_close"].isna().any() or candidates["previous_close"].le(0).any():
        raise ValueError("daily pre-screen contains invalid previous_close values")

    target_column = _first_present(candidates.columns, ("target_holdings",))
    target_holdings = config.LIVE_MAX_HOLDINGS if target_column is None else int(candidates[target_column].iloc[0])
    if not config.LIVE_MIN_HOLDINGS <= target_holdings <= config.LIVE_MAX_HOLDINGS:
        raise ValueError("daily pre-screen target_holdings must be between 5 and 10")

    merged = candidates.merge(prices, on="code", how="left", validate="one_to_one")
    merged["opening_gap"] = merged["open_price"] / merged["previous_close"] - 1
    safe = merged[merged["opening_gap"] <= max_gap_from_close].sort_values(["rank", "code"])
    selected = safe.head(target_holdings).copy()
    if len(selected) < config.LIVE_MIN_HOLDINGS:
        raise ValueError(
            f"only {len(selected)} pre-screen stocks opened within the {max_gap_from_close:.1%} gap guardrail; no order file was created"
        )

    orders = build_buy_recommendations(
        selected.drop(columns=["open_price", "opening_gap"], errors="ignore"),
        selected[["code", "open_price"]],
        total_assets=total_assets,
        open_premium=open_premium,
        max_gap_from_close=max_gap_from_close,
    )
    details = selected.set_index("code")[["opening_gap"]]
    orders["opening_gap"] = orders["code"].map(details["opening_gap"])
    orders["target_holdings"] = target_holdings
    orders["selected_holdings"] = len(selected)
    return orders


def load_open_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"open-price file not found: {path}")
    return normalise_open_prices(pd.read_csv(path, dtype=str))


def save_order_files(portfolio: pd.DataFrame, recommendations: pd.DataFrame | None = None) -> None:
    """Save the input template and, when available, the executable order table."""
    config.ensure_directories()
    build_open_price_template(portfolio).to_csv(config.OPEN_PRICE_TEMPLATE_PATH, index=False, encoding="utf-8-sig")
    if recommendations is not None:
        recommendations.to_csv(config.BUY_RECOMMENDATION_PATH, index=False, encoding="utf-8-sig")
