from __future__ import annotations

import json
import logging
import os

import numpy as np
import pandas as pd

from . import config


LOGGER = logging.getLogger(__name__)
os.environ.setdefault("MPLCONFIGDIR", str(config.ROOT_DIR / ".matplotlib"))


def calc_performance(
    nav: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    nav_col: str = "nav",
    ret_col: str = "net_ret",
    annualization_periods: int = 252,
) -> dict[str, float]:
    """Calculate core performance metrics from a net asset value curve."""
    if nav.empty:
        raise ValueError("nav is empty")

    nav_series = nav[nav_col].astype(float)
    daily_ret = nav[ret_col].astype(float) if ret_col in nav else nav_series.pct_change().fillna(0)
    cumulative_return = nav_series.iloc[-1] - 1
    annual_return = nav_series.iloc[-1] ** (annualization_periods / max(len(nav_series), 1)) - 1
    annual_vol = daily_ret.std(ddof=0) * np.sqrt(annualization_periods)
    sharpe = annual_return / annual_vol if annual_vol else 0.0
    max_drawdown = (nav_series / nav_series.cummax() - 1).min()
    win_rate = (daily_ret > 0).mean()
    month_nav = nav.assign(month=pd.to_datetime(nav["date"]).dt.to_period("M")).groupby("month")[nav_col].last()
    month_ret = month_nav.pct_change().dropna()
    monthly_win_rate = (month_ret > 0).mean() if len(month_ret) else 0.0
    avg_turnover = float(trades["turnover"].mean()) if trades is not None and not trades.empty else 0.0

    return {
        "cumulative_return": float(cumulative_return),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_drawdown),
        "win_rate": float(win_rate),
        "monthly_win_rate": float(monthly_win_rate),
        "avg_turnover": avg_turnover,
    }


def compare_performance(
    strategy: dict[str, float],
    benchmark: dict[str, float],
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
) -> dict[str, float]:
    """Build relative metrics between strategy and benchmark NAV curves."""
    merged = nav[["date", "nav"]].merge(benchmark_nav[["date", "benchmark_nav"]], on="date", how="inner")
    if merged.empty:
        return {}
    relative_nav = merged["nav"].iloc[-1] / merged["benchmark_nav"].iloc[-1]
    return {
        "excess_cumulative_return": strategy["cumulative_return"] - benchmark["cumulative_return"],
        "excess_annual_return": strategy["annual_return"] - benchmark["annual_return"],
        "relative_nav": float(relative_nav),
        "relative_return": float(relative_nav - 1),
    }


def flatten_performance(
    strategy: dict[str, float],
    benchmark: dict[str, float] | None = None,
    comparison: dict[str, float] | None = None,
) -> dict[str, float]:
    """Keep performance.json easy to inspect while adding benchmark fields."""
    result = dict(strategy)
    if benchmark:
        result.update({f"benchmark_{key}": value for key, value in benchmark.items()})
    if comparison:
        result.update(comparison)
    return result


def plot_nav(
    nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame | None = None,
    output_path=config.NAV_PLOT_PATH,
) -> None:
    """Plot and save the NAV curve."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(pd.to_datetime(nav["date"]), nav["nav"], label="Strategy NAV", color="#1f77b4")
    if benchmark_nav is not None and not benchmark_nav.empty:
        ax.plot(
            pd.to_datetime(benchmark_nav["date"]),
            benchmark_nav["benchmark_nav"],
            label="Equal-Weight Benchmark",
            color="#ff7f0e",
            alpha=0.85,
        )
    ax.set_title("Strategy NAV")
    ax.set_xlabel("Date")
    ax.set_ylabel("NAV")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def export_report(result: dict) -> None:
    """Save NAV, trades, metrics and the NAV chart."""
    config.ensure_directories()
    nav = result["nav"]
    trades = result["trades"]
    performance = result["performance"]
    benchmark_nav = result.get("benchmark")

    nav.to_csv(config.NAV_PATH, index=False, encoding="utf-8-sig")
    if benchmark_nav is not None and not benchmark_nav.empty:
        benchmark_nav.to_csv(config.BENCHMARK_NAV_PATH, index=False, encoding="utf-8-sig")
    trades.to_csv(config.TRADES_PATH, index=False, encoding="utf-8-sig")
    with config.PERFORMANCE_PATH.open("w", encoding="utf-8") as f:
        json.dump(performance, f, ensure_ascii=False, indent=2)
    plot_nav(nav, benchmark_nav=benchmark_nav)
    refresh_dashboard_data()
    LOGGER.info("saved report files to %s", config.RESULT_DIR)


def _csv_records(path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    if limit is not None:
        frame = frame.tail(limit)
    # pandas provides a JSON-safe conversion for timestamps, NaN and numpy types.
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def refresh_dashboard_data() -> None:
    """Write a local JavaScript data snapshot consumed by the static dashboard."""
    performance: dict = {}
    if config.PERFORMANCE_PATH.exists():
        with config.PERFORMANCE_PATH.open("r", encoding="utf-8") as f:
            performance = json.load(f)
    payload = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "performance": performance,
        "nav": _csv_records(config.NAV_PATH, limit=300),
        "benchmark": _csv_records(config.BENCHMARK_NAV_PATH, limit=300),
        "trades": _csv_records(config.TRADES_PATH, limit=30),
        "portfolio": _csv_records(config.LATEST_PORTFOLIO_PATH, limit=config.TOP_N),
        "prescreen": _csv_records(config.PRESCREEN_CANDIDATES_PATH, limit=config.TOP_N),
        "live_open_prices": _csv_records(config.LIVE_OPEN_PRICE_PATH, limit=config.TOP_N),
        "buy_recommendations": _csv_records(config.BUY_RECOMMENDATION_PATH, limit=config.LIVE_MAX_HOLDINGS),
        "tracking": _csv_records(config.PREDICTION_TRACKING_PATH, limit=60),
    }
    config.DISPLAY_DIR.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    config.DASHBOARD_DATA_PATH.write_text(f"window.QUANT_DASHBOARD_DATA = {serialized};\n", encoding="utf-8")
