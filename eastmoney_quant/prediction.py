from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pandas as pd

from . import config
from .data_fetch import fetch_one_stock_history
from .factor import build_factors
from .orders import save_order_files
from .report import refresh_dashboard_data
from .signal import build_frequency_signal, filter_stock_pool
from .trading_calendar import next_a_share_trading_day


LOGGER = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    portfolio: pd.DataFrame
    prescreen: pd.DataFrame
    tracking: pd.DataFrame
    summary: dict


def load_factor_data(refresh: bool = False) -> pd.DataFrame:
    """Load factor data, rebuilding it from clean data when needed."""
    if config.FACTOR_DAILY_PATH.exists() and not refresh:
        return pd.read_parquet(config.FACTOR_DAILY_PATH)
    if not config.CLEAN_DAILY_PATH.exists():
        raise FileNotFoundError("clean_daily.parquet not found; run `python main.py` first")
    clean = pd.read_parquet(config.CLEAN_DAILY_PATH)
    factors = build_factors(clean)
    factors.to_parquet(config.FACTOR_DAILY_PATH, index=False)
    return factors


def live_holding_regime(factor_data: pd.DataFrame, latest_date: pd.Timestamp) -> tuple[str, float, int]:
    """Choose a 5--10-name live list from the latest observable market breadth."""
    daily = factor_data[pd.to_datetime(factor_data["date"]) == latest_date]
    pool = filter_stock_pool(daily)
    if "trend_120" not in pool.columns:
        breadth = 0.0
    else:
        trend = pd.to_numeric(pool["trend_120"], errors="coerce")
        breadth = float((trend > 0).mean()) if trend.notna().any() else 0.0
    if breadth >= config.LIVE_STRONG_BREADTH:
        return "strong", breadth, config.LIVE_MAX_HOLDINGS
    if breadth >= config.LIVE_GOOD_BREADTH:
        return "good", breadth, config.LIVE_GOOD_HOLDINGS
    if breadth >= config.LIVE_NEUTRAL_BREADTH:
        return "neutral", breadth, config.LIVE_NEUTRAL_HOLDINGS
    return "defensive", breadth, config.LIVE_MIN_HOLDINGS


def build_daily_prescreen(factor_data: pd.DataFrame, candidate_count: int = config.TOP_N) -> pd.DataFrame:
    """Rank the latest tradable universe into a manual-review list of 20 names."""
    if candidate_count < config.LIVE_MAX_HOLDINGS:
        raise ValueError("candidate_count must allow at least the maximum 10-name live portfolio")
    latest_date = pd.to_datetime(factor_data["date"]).max()
    regime, market_breadth, target_holdings = live_holding_regime(factor_data, latest_date)
    pool = filter_stock_pool(factor_data[pd.to_datetime(factor_data["date"]) == latest_date])
    selected = pool.sort_values(["score", "code"], ascending=[False, True]).head(candidate_count).copy()
    if len(selected) < config.LIVE_MIN_HOLDINGS:
        raise ValueError(f"fewer than {config.LIVE_MIN_HOLDINGS} stocks passed the latest pre-screen")
    selected["rank"] = range(1, len(selected) + 1)
    selected["signal_date"] = latest_date
    selected["execution_date"] = next_a_share_trading_day(latest_date)
    # Factor prices are back-adjusted and must never be used as real order
    # prices.  The unadjusted prior close is filled separately before output.
    selected["research_close"] = selected["close"]
    selected["previous_close"] = pd.NA
    selected["market_regime"] = regime
    selected["market_breadth"] = market_breadth
    selected["target_holdings"] = target_holdings
    cols = [
        "signal_date",
        "execution_date",
        "rank",
        "code",
        "name",
        "score",
        "research_close",
        "previous_close",
        "market_regime",
        "market_breadth",
        "target_holdings",
        "mom_120_20",
        "trend_120",
        "rev_10",
        "vol_60",
        "drawdown_120",
        "amount_ma20",
    ]
    return selected[[column for column in cols if column in selected.columns]].reset_index(drop=True)


def enrich_prescreen_execution_prices(
    prescreen: pd.DataFrame,
    history_source: str = "tencent",
) -> pd.DataFrame:
    """Attach real, unadjusted signal-day closes for the manual order workflow."""
    if prescreen.empty:
        return prescreen.copy()
    signal_date = pd.to_datetime(prescreen["signal_date"].iloc[0]).strftime("%Y%m%d")
    result = prescreen.copy()
    references: dict[str, float] = {}
    for code in result["code"].astype(str).str.zfill(6):
        try:
            history = fetch_one_stock_history(
                code=code,
                start_date=signal_date,
                end_date=signal_date,
                adjust="",
                source=history_source,
            )
            if history.empty:
                continue
            history["date"] = pd.to_datetime(history["date"], errors="coerce")
            row = history[history["date"] == pd.to_datetime(signal_date)]
            if row.empty:
                continue
            close = pd.to_numeric(row.iloc[-1].get("close"), errors="coerce")
            if pd.notna(close) and close > 0:
                references[code] = float(close)
        except Exception as exc:  # pragma: no cover - network/API instability
            LOGGER.warning("unadjusted execution reference unavailable for %s: %s", code, exc)
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["previous_close"] = result["code"].map(references)
    result["reference_price_source"] = result["previous_close"].notna().map(
        {True: f"{history_source}_unadjusted", False: "unavailable"}
    )
    return result


def build_latest_portfolio(
    factor_data: pd.DataFrame,
    top_n: int = config.LIVE_MAX_HOLDINGS,
) -> pd.DataFrame:
    """Build a breadth-aware 5--10-name portfolio for an empty account."""
    if not config.LIVE_MIN_HOLDINGS <= top_n <= config.LIVE_MAX_HOLDINGS:
        raise ValueError(f"top_n must be between {config.LIVE_MIN_HOLDINGS} and {config.LIVE_MAX_HOLDINGS}")
    latest_date = pd.to_datetime(factor_data["date"]).max()
    regime, market_breadth, regime_holdings = live_holding_regime(factor_data, latest_date)
    selected_count = min(top_n, regime_holdings)
    controlled_signals = build_frequency_signal(
        factor_data,
        frequency=config.REBALANCE_FREQUENCY,
        top_n=selected_count,
        reserve_latest=False,
    )
    selected = controlled_signals[controlled_signals["date"] == latest_date].copy()
    if selected.empty:
        raise ValueError(f"no tradable stocks passed filters on {latest_date.date()}")
    latest_factors = factor_data[pd.to_datetime(factor_data["date"]) == latest_date].copy()
    selected = selected.merge(
        latest_factors.drop(columns=["name", "score"], errors="ignore"),
        on=["date", "code"],
        how="left",
    )
    selected["signal_date"] = latest_date
    selected["execution_date"] = next_a_share_trading_day(latest_date)
    selected["reference_close"] = selected["close"]
    selected["target_budget"] = config.ORDER_TOTAL_ASSETS / len(selected)
    selected["market_regime"] = regime
    selected["market_breadth"] = market_breadth
    cols = [
        "signal_date",
        "execution_date",
        "rank",
        "code",
        "name",
        "score",
        "reference_close",
        "target_budget",
        "market_regime",
        "market_breadth",
        "mom_120_20",
        "trend_120",
        "rev_10",
        "vol_60",
        "drawdown_120",
        "amount_ma20",
    ]
    return selected[[col for col in cols if col in selected.columns]].reset_index(drop=True)


def _load_prediction_log() -> pd.DataFrame:
    if not config.PREDICTION_LOG_PATH.exists():
        return pd.DataFrame()
    log = pd.read_csv(config.PREDICTION_LOG_PATH, parse_dates=["signal_date"], dtype={"code": str})
    log["code"] = log["code"].astype(str).str.zfill(6)
    return log


def append_prediction_log(portfolio: pd.DataFrame) -> pd.DataFrame:
    """Append the latest portfolio to the prediction log, replacing same-date reruns."""
    old = _load_prediction_log()
    current = portfolio.copy()
    current["code"] = current["code"].astype(str).str.zfill(6)
    if "weight" not in current.columns:
        if "target_budget" not in current.columns:
            raise ValueError("portfolio must contain weight or target_budget for tracking")
        target_total = pd.to_numeric(current["target_budget"], errors="coerce").sum()
        if not target_total > 0:
            raise ValueError("portfolio target_budget must sum to a positive value")
        current["weight"] = pd.to_numeric(current["target_budget"], errors="coerce") / target_total
    current["created_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    if not old.empty:
        old = old[pd.to_datetime(old["signal_date"]) != pd.to_datetime(current["signal_date"].iloc[0])]
        log = pd.concat([old, current], ignore_index=True)
    else:
        log = current
    log = log.sort_values(["signal_date", "rank"]).reset_index(drop=True)
    config.ensure_directories()
    log.to_csv(config.PREDICTION_LOG_PATH, index=False, encoding="utf-8-sig")
    return log


def _forward_return_for_one_signal(
    price_data: pd.DataFrame,
    signal: pd.DataFrame,
    latest_date: pd.Timestamp,
) -> dict | None:
    signal_date = pd.to_datetime(signal["signal_date"].iloc[0])
    if latest_date <= signal_date:
        return None

    prices = price_data[price_data["code"].isin(signal["code"])].copy()
    prices = prices[(prices["date"] >= signal_date) & (prices["date"] <= latest_date)]
    if prices.empty:
        return None

    start = prices[prices["date"] == signal_date][["code", "close"]].rename(columns={"close": "start_close"})
    end = prices.sort_values("date").groupby("code").tail(1)[["code", "date", "close"]]
    allocation_column = "weight" if "weight" in signal.columns else "target_budget"
    allocations = pd.to_numeric(signal[allocation_column], errors="coerce")
    allocation_total = allocations.sum()
    if not allocation_total > 0:
        return None
    positions = signal[["code"]].copy()
    positions["weight"] = allocations / allocation_total
    merged = positions.merge(start, on="code", how="inner").merge(end, on="code", how="inner")
    merged = merged[(merged["start_close"] > 0) & (merged["close"] > 0)]
    if merged.empty:
        return None

    merged["stock_return"] = merged["close"] / merged["start_close"] - 1
    portfolio_return = (merged["weight"] * merged["stock_return"]).sum()
    return {
        "signal_date": signal_date,
        "as_of_date": latest_date,
        "holding_days": int(prices["date"].nunique() - 1),
        "covered_holdings": int(len(merged)),
        "target_holdings": int(len(signal)),
        "portfolio_return": float(portfolio_return),
        "best_stock_return": float(merged["stock_return"].max()),
        "worst_stock_return": float(merged["stock_return"].min()),
    }


def track_logged_predictions(factor_data: pd.DataFrame, log: pd.DataFrame | None = None) -> pd.DataFrame:
    """Mark previous prediction logs to the latest available close."""
    columns = [
        "signal_date",
        "as_of_date",
        "holding_days",
        "covered_holdings",
        "target_holdings",
        "portfolio_return",
        "best_stock_return",
        "worst_stock_return",
    ]
    if log is None:
        log = _load_prediction_log()
    if log.empty:
        return pd.DataFrame(columns=columns)

    price_data = factor_data[["date", "code", "close"]].copy()
    price_data["date"] = pd.to_datetime(price_data["date"])
    price_data["code"] = price_data["code"].astype(str).str.zfill(6)
    log = log.copy()
    log["code"] = log["code"].astype(str).str.zfill(6)
    latest_date = price_data["date"].max()
    records = []
    for signal_date, signal in log.groupby(pd.to_datetime(log["signal_date"])):
        result = _forward_return_for_one_signal(price_data, signal, latest_date)
        if result is not None:
            records.append(result)
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records).sort_values("signal_date").reset_index(drop=True)


def save_prediction_outputs(
    portfolio: pd.DataFrame,
    prescreen: pd.DataFrame,
    tracking: pd.DataFrame,
    summary: dict,
) -> None:
    config.ensure_directories()
    portfolio.to_csv(config.LATEST_PORTFOLIO_PATH, index=False, encoding="utf-8-sig")
    prescreen.to_csv(config.PRESCREEN_CANDIDATES_PATH, index=False, encoding="utf-8-sig")
    save_order_files(prescreen)
    tracking.to_csv(config.PREDICTION_TRACKING_PATH, index=False, encoding="utf-8-sig")
    with config.PREDICTION_SUMMARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    refresh_dashboard_data()


def run_prediction(
    top_n: int = config.LIVE_MAX_HOLDINGS,
    refresh_factors: bool = False,
    append_log: bool = True,
    refresh_execution_prices: bool = True,
) -> PredictionResult:
    """Generate latest portfolio and track older prediction logs."""
    factor_data = load_factor_data(refresh=refresh_factors)
    factor_data["date"] = pd.to_datetime(factor_data["date"])
    portfolio = build_latest_portfolio(factor_data, top_n=top_n)
    prescreen = build_daily_prescreen(factor_data)
    if refresh_execution_prices:
        prescreen = enrich_prescreen_execution_prices(prescreen)

    existing_log = _load_prediction_log()
    tracking_before_append = track_logged_predictions(factor_data, existing_log)
    log = append_prediction_log(portfolio) if append_log else existing_log
    tracking = track_logged_predictions(factor_data, log)

    latest_date = pd.to_datetime(portfolio["signal_date"].iloc[0])
    latest_tracking = tracking[tracking["signal_date"] == latest_date]
    previous_tracking = tracking_before_append.tail(1)
    summary = {
        "signal_date": latest_date.date().isoformat(),
        "execution_date": pd.to_datetime(portfolio["execution_date"].iloc[0]).date().isoformat(),
        "intended_action": "Start from an empty account on the next trading day after supplying its opening prices.",
        "top_n": int(len(portfolio)),
        "market_regime": str(portfolio["market_regime"].iloc[0]),
        "market_breadth": float(portfolio["market_breadth"].iloc[0]),
        "total_assets": config.ORDER_TOTAL_ASSETS,
        "latest_portfolio_path": str(config.LATEST_PORTFOLIO_PATH),
        "prescreen_path": str(config.PRESCREEN_CANDIDATES_PATH),
        "prescreen_count": int(len(prescreen)),
        "open_price_template_path": str(config.OPEN_PRICE_TEMPLATE_PATH),
        "buy_recommendation_path": str(config.BUY_RECOMMENDATION_PATH),
        "prediction_log_path": str(config.PREDICTION_LOG_PATH),
        "tracking_path": str(config.PREDICTION_TRACKING_PATH),
        "has_current_tracking": bool(not latest_tracking.empty),
        "previous_tracking": previous_tracking.to_dict(orient="records"),
    }
    save_prediction_outputs(portfolio, prescreen, tracking, summary)
    return PredictionResult(portfolio=portfolio, prescreen=prescreen, tracking=tracking, summary=summary)
