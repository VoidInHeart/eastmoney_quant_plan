from __future__ import annotations

import json
import logging
import os

import numpy as np
import pandas as pd

from . import config


LOGGER = logging.getLogger(__name__)
os.environ.setdefault("MPLCONFIGDIR", str(config.ROOT_DIR / ".matplotlib"))


def calc_performance(nav: pd.DataFrame, trades: pd.DataFrame | None = None) -> dict[str, float]:
    """Calculate core performance metrics from a net asset value curve."""
    if nav.empty:
        raise ValueError("nav is empty")

    nav_series = nav["nav"].astype(float)
    daily_ret = nav["net_ret"].astype(float) if "net_ret" in nav else nav_series.pct_change().fillna(0)
    cumulative_return = nav_series.iloc[-1] - 1
    annual_return = nav_series.iloc[-1] ** (252 / max(len(nav_series), 1)) - 1
    annual_vol = daily_ret.std(ddof=0) * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol else 0.0
    max_drawdown = (nav_series / nav_series.cummax() - 1).min()
    win_rate = (daily_ret > 0).mean()
    month_nav = nav.assign(month=pd.to_datetime(nav["date"]).dt.to_period("M")).groupby("month")["nav"].last()
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


def plot_nav(nav: pd.DataFrame, output_path=config.NAV_PLOT_PATH) -> None:
    """Plot and save the NAV curve."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(pd.to_datetime(nav["date"]), nav["nav"], label="Strategy NAV", color="#1f77b4")
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

    nav.to_csv(config.NAV_PATH, index=False, encoding="utf-8-sig")
    trades.to_csv(config.TRADES_PATH, index=False, encoding="utf-8-sig")
    with config.PERFORMANCE_PATH.open("w", encoding="utf-8") as f:
        json.dump(performance, f, ensure_ascii=False, indent=2)
    plot_nav(nav)
    LOGGER.info("saved report files to %s", config.RESULT_DIR)
