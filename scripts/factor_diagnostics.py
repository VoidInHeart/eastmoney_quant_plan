from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config


FACTORS = ["mom_120_20", "trend_120", "rev_10", "vol_60", "drawdown_120"]
HORIZONS = [1, 5, 10, 20, 40]
START_DATE = pd.Timestamp("2021-01-01")


def _daily_rank_ic(frame: pd.DataFrame, factor: str, forward_col: str) -> pd.Series:
    valid = frame[["date", factor, forward_col]].dropna()
    ranks = pd.DataFrame(
        {
            "date": valid["date"].to_numpy(),
            "factor_rank": valid.groupby("date")[factor].rank().to_numpy(),
            "return_rank": valid.groupby("date")[forward_col].rank().to_numpy(),
        }
    )
    correlations = ranks.groupby("date")[["factor_rank", "return_rank"]].corr()
    return correlations.xs("factor_rank", level=1)["return_rank"].rename("rank_ic")


def _half_year(date: pd.Series) -> pd.Series:
    return date.dt.year.astype(str) + "H" + (((date.dt.month - 1) // 6) + 1).astype(str)


def main() -> None:
    columns = ["date", "code", "close", *FACTORS]
    data = pd.read_parquet(config.FACTOR_DAILY_PATH, columns=columns, filters=[("date", ">=", START_DATE)])
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values(["code", "date"]).reset_index(drop=True)

    rank_ic_rows: list[pd.DataFrame] = []
    group_rows: list[dict[str, object]] = []
    decay_rows: list[dict[str, object]] = []
    regime_rows: list[dict[str, object]] = []
    by_code = data.groupby("code", group_keys=False)

    for horizon in HORIZONS:
        forward_col = f"forward_{horizon}d"
        data[forward_col] = by_code["close"].shift(-horizon) / data["close"] - 1
        for factor in FACTORS:
            daily_ic = _daily_rank_ic(data, factor, forward_col).reset_index()
            daily_ic["factor"] = factor
            daily_ic["horizon_days"] = horizon
            daily_ic["rolling_6m_rank_ic"] = daily_ic["rank_ic"].rolling(126, min_periods=63).mean()
            rank_ic_rows.append(daily_ic)
            std = daily_ic["rank_ic"].std(ddof=0)
            decay_rows.append(
                {
                    "factor": factor,
                    "horizon_days": horizon,
                    "rank_ic_mean": daily_ic["rank_ic"].mean(),
                    "rank_ic_std": std,
                    "icir": daily_ic["rank_ic"].mean() / std if std else 0.0,
                    "positive_ratio": (daily_ic["rank_ic"] > 0).mean(),
                    "observations": len(daily_ic),
                }
            )
            regime = daily_ic.assign(half_year=_half_year(daily_ic["date"])).groupby("half_year")["rank_ic"].agg(["mean", "std", "count"])
            for period, values in regime.iterrows():
                regime_rows.append(
                    {
                        "factor": factor,
                        "horizon_days": horizon,
                        "half_year": period,
                        "rank_ic_mean": values["mean"],
                        "rank_ic_std": values["std"],
                        "observations": int(values["count"]),
                    }
                )

            valid = data[["date", factor, forward_col]].dropna().copy()
            percentile = valid.groupby("date")[factor].rank(pct=True)
            valid["group"] = np.ceil(percentile * 5).clip(1, 5).astype(int)
            daily_groups = valid.groupby(["date", "group"])[forward_col].mean().rename("forward_return").reset_index()
            summary = daily_groups.groupby("group")["forward_return"].agg(["mean", "std", "count"])
            pivot = daily_groups.pivot(index="date", columns="group", values="forward_return")
            spread = (pivot.get(5) - pivot.get(1)).dropna()
            for group, values in summary.iterrows():
                group_rows.append(
                    {
                        "factor": factor,
                        "horizon_days": horizon,
                        "group": int(group),
                        "mean_forward_return": values["mean"],
                        "std_forward_return": values["std"],
                        "observations": int(values["count"]),
                        "top_minus_bottom_mean": spread.mean(),
                        "top_minus_bottom_positive_ratio": (spread > 0).mean(),
                    }
                )
        data.drop(columns=[forward_col], inplace=True)

    rank_ic = pd.concat(rank_ic_rows, ignore_index=True)
    rank_ic.to_csv(config.RESULT_DIR / "factor_rank_ic.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(group_rows).to_csv(config.RESULT_DIR / "factor_group_returns.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(decay_rows).to_csv(config.RESULT_DIR / "factor_decay.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(regime_rows).to_csv(config.RESULT_DIR / "factor_regime_performance.csv", index=False, encoding="utf-8-sig")

    correlations = data.groupby("date")[FACTORS].corr(method="spearman")
    average_correlation = correlations.stack().groupby(level=[1, 2]).mean().rename("average_daily_spearman").reset_index()
    average_correlation.columns = ["factor_left", "factor_right", "average_daily_spearman"]
    average_correlation.to_csv(config.RESULT_DIR / "factor_correlation.csv", index=False, encoding="utf-8-sig")

    neutralization = pd.DataFrame(
        [
            {"requirement": "historical_industry", "status": "unavailable", "detail": "当前因子数据没有历史行业归属。"},
            {"requirement": "historical_market_cap", "status": "unavailable", "detail": "当前因子数据没有历史市值字段。"},
            {"requirement": "beta_exposure", "status": "pending", "detail": "可在补充市场指数和无风险收益后计算。"},
        ]
    )
    neutralization.to_csv(config.RESULT_DIR / "factor_neutralization_availability.csv", index=False, encoding="utf-8-sig")
    print(f"Saved factor diagnostics to {config.RESULT_DIR}")


if __name__ == "__main__":
    main()
