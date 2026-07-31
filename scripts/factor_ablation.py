"""Pre-registered factor ablations under the stricter execution engine.

The script intentionally reports every fixed candidate and does not select a
winner from its test split.  E4/E5 remain unavailable until point-in-time
industry and market-cap panels are fully backfilled.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.execution import run_executable_backtest
from eastmoney_quant.factor import cross_sectional_rank_score
from eastmoney_quant.signal import build_frequency_signal, build_interval_signal
from eastmoney_quant.universe import build_observed_universe, universe_coverage_summary


DATA_COLUMNS = [
    "date",
    "code",
    "name",
    "open",
    "close",
    "amount_ma20",
    "listing_days",
    "trend_120",
    "drawdown_120",
    "rev_10",
    "vol_60",
    "score",
]
RESEARCH_SPLITS = {
    "train": ("2021-07-01", "2023-12-29"),
    "validation": ("2024-01-02", "2024-12-31"),
    # This period has already been inspected in earlier research.  It is kept
    # only for transparent comparison, not represented as a fresh final test.
    "observed_research": ("2025-01-02", "2026-07-10"),
}


@dataclass(frozen=True)
class Candidate:
    name: str
    factors: tuple[tuple[str, float], ...]
    rules: str


CANDIDATES = (
    Candidate("E0_five_factor_baseline", (), "baseline"),
    Candidate("E1_low_volatility_v2", (("vol_60", 1.0),), "v2"),
    Candidate("E2_short_reversal_v2", (("rev_10", 1.0),), "v2"),
    Candidate("E3_reversal_lowvol_v2", (("rev_10", 0.5), ("vol_60", 0.5)), "v2"),
)
V2_POOL_OVERRIDES = {"max_vol_quantile": None, "min_trend_120": None, "min_drawdown_120": None}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pre-registered E0-E3 factor ablations")
    parser.add_argument("--dry-run", action="store_true", help="print fixed candidates without loading data")
    parser.add_argument("--one-way-cost-bps", type=float, default=20.0, help="symmetric cost charged on every fill")
    parser.add_argument("--max-order-attempts", type=int, default=5, help="sessions before pending-order cancellation")
    parser.add_argument("--allow-partial-metadata", action="store_true", help="research only: run despite incomplete suspension/industry/share coverage")
    parser.add_argument("--rank-ic-only", action="store_true", help="only rebuild the training-window factor RankIC diagnostic")
    return parser.parse_args()


def score_candidate(data: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    result = data.copy()
    if candidate.rules == "baseline":
        return result
    factor_columns = [column for column, _ in candidate.factors]
    result = cross_sectional_rank_score(result, factor_columns)
    result["score"] = sum(result[f"{column}_rank"] * weight for column, weight in candidate.factors)
    return result


def build_candidate_signal(data: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    trading_days = max(2, int(data["date"].nunique()) - 1)
    if candidate.rules == "baseline":
        return build_frequency_signal(data, frequency="weekly", trading_days=trading_days)
    return build_interval_signal(
        data,
        interval_days=10,
        trading_days=trading_days,
        top_n=30,
        rank_buffer=30,
        max_replacements=3,
        min_holding_days=10,
        min_score_improvement=None,
        pool_filter_overrides=V2_POOL_OVERRIDES,
    )


def training_rank_ic(data: pd.DataFrame) -> pd.DataFrame:
    """Compute only the training-window 10-day RankIC used for diagnosis."""
    result = data.sort_values(["code", "date"]).copy()
    result["forward_return_10"] = result.groupby("code")["close"].shift(-10) / result["close"] - 1
    rows = []
    for factor in ("rev_10", "vol_60"):
        values = []
        for _, group in result[["date", factor, "forward_return_10"]].dropna().groupby("date"):
            if len(group) >= 10:
                # Spearman correlation is Pearson correlation of within-date
                # ranks.  Keep this dependency-free so diagnostics run in the
                # project's lightweight environment.
                values.append(group[factor].rank().corr(group["forward_return_10"].rank()))
        rows.append({"factor": factor, "horizon_days": 10, "rank_ic_mean": pd.Series(values).mean(), "observations": len(values)})
    return pd.DataFrame(rows)


def execution_metadata_is_complete(raw: pd.DataFrame) -> bool:
    """E0-E3 need complete suspension coverage, not industry/share panels.

    Industry and market-cap panels are required only by E4 neutralization.  A
    stricter all-metadata gate here would incorrectly prevent testing the
    pre-registered low-volatility/reversal candidates.
    """
    expected_dates = set(pd.to_datetime(raw["date"]).dropna().drop_duplicates())
    completed_dates: set[pd.Timestamp] = set()
    if config.SUSPENSION_FETCH_LOG_PATH.exists():
        log = pd.read_parquet(config.SUSPENSION_FETCH_LOG_PATH, columns=["requested_date", "status"])
        completed_dates |= set(pd.to_datetime(log.loc[log["status"].eq("success"), "requested_date"], errors="coerce").dropna())
    if config.SUSPENSION_PATH.exists():
        events = pd.read_parquet(config.SUSPENSION_PATH, columns=["requested_date"])
        completed_dates |= set(pd.to_datetime(events["requested_date"], errors="coerce").dropna())
    return expected_dates.issubset(completed_dates)


def main() -> None:
    args = parse_args()
    if args.dry_run:
        for candidate in CANDIDATES:
            print(f"{candidate.name}: factors={candidate.factors or 'frozen composite'}, rules={candidate.rules}")
        return

    raw = pd.read_parquet(config.FACTOR_DAILY_PATH, columns=DATA_COLUMNS)
    raw["date"] = pd.to_datetime(raw["date"])
    if not execution_metadata_is_complete(raw) and not args.allow_partial_metadata:
        raise SystemExit(
            "suspension coverage is incomplete; run scripts/metadata_coverage.py and backfill first, "
            "or use --allow-partial-metadata for a clearly labelled research-only run"
        )
    universe = build_observed_universe(raw)
    coverage = universe_coverage_summary(universe)
    (config.RESULT_DIR / "factor_ablation_universe_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.rank_ic_only:
        train = universe[(universe["date"] >= RESEARCH_SPLITS["train"][0]) & (universe["date"] <= RESEARCH_SPLITS["train"][1])]
        training_rank_ic(train).to_csv(config.RESULT_DIR / "factor_ablation_training_rank_ic.csv", index=False, encoding="utf-8-sig")
        print("Saved training RankIC diagnostic", flush=True)
        return
    suspensions = pd.read_parquet(config.SUSPENSION_PATH) if config.SUSPENSION_PATH.exists() else None
    rows: list[dict[str, object]] = []
    for split, (start, end) in RESEARCH_SPLITS.items():
        split_data = universe[(universe["date"] >= start) & (universe["date"] <= end)].copy()
        split_data = split_data[split_data["is_observed_tradable"]].copy()
        for candidate in CANDIDATES:
            scored = score_candidate(split_data, candidate)
            signal = build_candidate_signal(scored, candidate)
            if signal["date"].nunique() < 2:
                rows.append({"split": split, "candidate": candidate.name, "status": "skipped", "reason": "insufficient_signal_dates"})
                continue
            result = run_executable_backtest(
                scored,
                signal,
                commission_rate=0.0,
                stamp_tax_rate=0.0,
                slippage_rate=args.one_way_cost_bps / 10_000,
                suspension_events=suspensions,
                max_order_attempts=args.max_order_attempts,
            )
            metrics = result["performance"]
            annual_turnover = float(result["trades"]["turnover"].sum() / max(len(result["nav"]), 1) * 252)
            rows.append(
                {
                    "split": split,
                    "start": start,
                    "end": end,
                    "candidate": candidate.name,
                    "rules": candidate.rules,
                    "factors": "+".join(column for column, _ in candidate.factors) or "current_five_factor_score",
                    "status": "completed",
                    "one_way_cost_bps": args.one_way_cost_bps,
                    "annual_return": metrics["annual_return"],
                    "relative_return": metrics.get("relative_return"),
                    "excess_annual_return": metrics.get("excess_annual_return"),
                    "sharpe": metrics["sharpe"],
                    "max_drawdown": metrics["max_drawdown"],
                    "annual_turnover": annual_turnover,
                    "total_cost": metrics["total_cost"],
                    "deferred_orders": metrics["deferred_order_events"],
                    "cancelled_orders": metrics["cancelled_order_events"],
                }
            )
            print(f"completed {split} / {candidate.name}", flush=True)

    pd.DataFrame(rows).to_csv(config.RESULT_DIR / "factor_ablation_v2.csv", index=False, encoding="utf-8-sig")
    availability = pd.DataFrame(
        [
            {"candidate": "E4_neutralized_reversal_lowvol", "status": "blocked", "reason": "仍缺少完整历史股本构造的全市场时点市值面板"},
            {"candidate": "E5_rolling_ridge", "status": "blocked", "reason": "先完成 E0-E3 与真实成交验证，再训练模型"},
        ]
    )
    availability.to_csv(config.RESULT_DIR / "factor_ablation_availability.csv", index=False, encoding="utf-8-sig")
    train = universe[(universe["date"] >= RESEARCH_SPLITS["train"][0]) & (universe["date"] <= RESEARCH_SPLITS["train"][1])]
    training_rank_ic(train).to_csv(config.RESULT_DIR / "factor_ablation_training_rank_ic.csv", index=False, encoding="utf-8-sig")
    print(f"Saved ablation outputs to: {config.RESULT_DIR}")


if __name__ == "__main__":
    main()
