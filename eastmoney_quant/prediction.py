from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from . import config
from .factor import build_factors
from .report import refresh_dashboard_data
from .signal import build_frequency_signal


@dataclass
class PredictionResult:
    portfolio: pd.DataFrame
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


def build_latest_portfolio(factor_data: pd.DataFrame, top_n: int = config.TOP_N) -> pd.DataFrame:
    """Build the latest strategy portfolio using the most recent trade date."""
    latest_date = pd.to_datetime(factor_data["date"]).max()
    controlled_signals = build_frequency_signal(
        factor_data,
        frequency=config.REBALANCE_FREQUENCY,
        top_n=top_n,
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
    cols = [
        "signal_date",
        "rank",
        "code",
        "name",
        "score",
        "weight",
        "close",
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
    merged = signal[["code", "weight"]].merge(start, on="code", how="inner").merge(end, on="code", how="inner")
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


def save_prediction_outputs(portfolio: pd.DataFrame, tracking: pd.DataFrame, summary: dict) -> None:
    config.ensure_directories()
    portfolio.to_csv(config.LATEST_PORTFOLIO_PATH, index=False, encoding="utf-8-sig")
    tracking.to_csv(config.PREDICTION_TRACKING_PATH, index=False, encoding="utf-8-sig")
    with config.PREDICTION_SUMMARY_PATH.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    refresh_dashboard_data()


def run_prediction(top_n: int = config.TOP_N, refresh_factors: bool = False, append_log: bool = True) -> PredictionResult:
    """Generate latest portfolio and track older prediction logs."""
    factor_data = load_factor_data(refresh=refresh_factors)
    factor_data["date"] = pd.to_datetime(factor_data["date"])
    portfolio = build_latest_portfolio(factor_data, top_n=top_n)

    existing_log = _load_prediction_log()
    tracking_before_append = track_logged_predictions(factor_data, existing_log)
    log = append_prediction_log(portfolio) if append_log else existing_log
    tracking = track_logged_predictions(factor_data, log)

    latest_date = pd.to_datetime(portfolio["signal_date"].iloc[0])
    latest_tracking = tracking[tracking["signal_date"] == latest_date]
    previous_tracking = tracking_before_append.tail(1)
    summary = {
        "signal_date": latest_date.date().isoformat(),
        "intended_action": "Use this as the portfolio after the latest close; evaluate after new data arrives.",
        "top_n": int(len(portfolio)),
        "latest_portfolio_path": str(config.LATEST_PORTFOLIO_PATH),
        "prediction_log_path": str(config.PREDICTION_LOG_PATH),
        "tracking_path": str(config.PREDICTION_TRACKING_PATH),
        "has_current_tracking": bool(not latest_tracking.empty),
        "previous_tracking": previous_tracking.to_dict(orient="records"),
    }
    save_prediction_outputs(portfolio, tracking, summary)
    return PredictionResult(portfolio=portfolio, tracking=tracking, summary=summary)
