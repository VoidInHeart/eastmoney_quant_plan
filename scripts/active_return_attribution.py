from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.backtest import _build_execution_plan, _target_position


def _period_return_attribution() -> pd.DataFrame:
    signal = pd.read_parquet(config.SIGNAL_PATH)
    signal["date"] = pd.to_datetime(signal["date"])
    signal["code"] = signal["code"].astype(str).str.zfill(6)
    factor_dates = pd.read_parquet(config.FACTOR_DAILY_PATH, columns=["date"])
    trade_dates = sorted(pd.to_datetime(factor_dates["date"].dropna().unique()))
    plan = _build_execution_plan(signal, trade_dates)
    execution_dates = [item["execution_date"] for item in plan]
    prices = pd.read_parquet(
        config.FACTOR_DAILY_PATH,
        columns=["date", "code", "open"],
        filters=[("date", "in", execution_dates)],
    )
    prices["date"] = pd.to_datetime(prices["date"])
    prices["code"] = prices["code"].astype(str).str.zfill(6)
    price_matrix = prices.pivot_table(index="date", columns="code", values="open", aggfunc="last")
    names = signal[["code", "name"]].drop_duplicates("code")

    rows = []
    for current, following in zip(plan, plan[1:]):
        start_date = current["execution_date"]
        end_date = following["execution_date"]
        position = _target_position(current["target"])
        codes = list(position)
        start = price_matrix.loc[start_date].reindex(codes)
        end = price_matrix.loc[end_date].reindex(codes)
        valid = start.notna() & end.notna() & (start > 0) & (end > 0)
        for code in valid[valid].index:
            stock_return = float(end[code] / start[code] - 1)
            rows.append(
                {
                    "signal_date": current["signal_date"],
                    "execution_date": start_date,
                    "period_end_date": end_date,
                    "code": code,
                    "weight": position[code],
                    "stock_return": stock_return,
                    "gross_contribution": position[code] * stock_return,
                }
            )
    detail = pd.DataFrame(rows).merge(names, on="code", how="left")
    detail.to_csv(config.RESULT_DIR / "individual_period_contribution.csv", index=False, encoding="utf-8-sig")
    return detail


def main() -> None:
    strategy = pd.read_csv(config.RESULT_DIR / "baseline_daily_returns.csv", parse_dates=["date"])
    benchmark = pd.read_csv(config.RESULT_DIR / "baseline_benchmark.csv", parse_dates=["date"])
    merged = strategy[["date", "net_ret"]].merge(benchmark[["date", "benchmark_ret"]], on="date", how="inner")
    active = merged["net_ret"] - merged["benchmark_ret"]
    variance = merged["benchmark_ret"].var(ddof=0)
    beta = float(merged["net_ret"].cov(merged["benchmark_ret"], ddof=0) / variance) if variance else np.nan
    annualization = json.loads(config.PERFORMANCE_PATH.read_text(encoding="utf-8")).get("annualization_periods", 52)
    tracking_error = float(active.std(ddof=0) * np.sqrt(annualization))
    active_annual = float((1 + active.mean()) ** annualization - 1)
    information_ratio = active_annual / tracking_error if tracking_error else np.nan
    up = merged[merged["benchmark_ret"] > 0]
    down = merged[merged["benchmark_ret"] < 0]
    summary = pd.DataFrame(
        [
            {
                "beta_to_benchmark": beta,
                "annualized_active_return": active_annual,
                "tracking_error": tracking_error,
                "information_ratio": information_ratio,
                "up_market_capture": up["net_ret"].mean() / up["benchmark_ret"].mean() if not up.empty else np.nan,
                "down_market_capture": down["net_ret"].mean() / down["benchmark_ret"].mean() if not down.empty else np.nan,
                "periods": len(merged),
            }
        ]
    )
    summary.to_csv(config.RESULT_DIR / "active_return_attribution.csv", index=False, encoding="utf-8-sig")

    contribution = _period_return_attribution()
    by_stock = contribution.groupby(["code", "name"], dropna=False).agg(
        gross_contribution=("gross_contribution", "sum"),
        periods=("period_end_date", "count"),
        average_weight=("weight", "mean"),
    ).reset_index().sort_values("gross_contribution", ascending=False)
    by_stock.to_csv(config.RESULT_DIR / "individual_contribution.csv", index=False, encoding="utf-8-sig")
    availability = pd.DataFrame(
        [
            {"attribution": "industry", "status": "unavailable", "detail": "缺少历史行业分类字段。"},
            {"attribution": "market_cap", "status": "unavailable", "detail": "缺少历史总市值和流通市值字段。"},
            {"attribution": "beta_and_stock_contribution", "status": "available", "detail": "已基于基准和开盘到开盘持有收益输出。"},
        ]
    )
    availability.to_csv(config.RESULT_DIR / "attribution_availability.csv", index=False, encoding="utf-8-sig")
    print(summary.to_dict(orient="records")[0])


if __name__ == "__main__":
    main()
