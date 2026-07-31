from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config
from eastmoney_quant.factor import build_factors


def _record(check: str, status: str, value: object, detail: str) -> dict[str, object]:
    return {"check": check, "status": status, "value": value, "detail": detail}


def main() -> None:
    clean = pd.read_parquet(config.CLEAN_DAILY_PATH)
    clean["date"] = pd.to_datetime(clean["date"])
    rows: list[dict[str, object]] = []
    duplicates = int(clean.duplicated(["date", "code"]).sum())
    rows.append(_record("date_code_duplicates", "pass" if duplicates == 0 else "fail", duplicates, "日期+代码必须唯一"))
    invalid_code = int((~clean["code"].astype(str).str.fullmatch(r"\d{6}").fillna(False)).sum())
    rows.append(_record("six_digit_codes", "pass" if invalid_code == 0 else "fail", invalid_code, "股票代码必须为六位数字"))
    invalid_prices = int(((clean["open"] <= 0) | (clean["close"] <= 0)).sum())
    rows.append(_record("valid_open_close", "pass" if invalid_prices == 0 else "fail", invalid_prices, "开盘与收盘价必须为正"))
    sequence_ok = clean.sort_values(["code", "date"]).groupby("code")["date"].apply(lambda dates: dates.is_monotonic_increasing).all()
    rows.append(_record("chronological_rows", "pass" if sequence_ok else "fail", bool(sequence_ok), "每只股票的日期必须单调递增"))

    names = clean["name"].fillna("").astype(str)
    delisted_rows = int(names.str.contains("退", regex=False).sum())
    rows.append(
        _record(
            "delisting_coverage",
            "warning",
            delisted_rows,
            "本地样本中名称含“退”的行数；为 0 不能证明无幸存者偏差，需历史时点股票池数据确认。",
        )
    )
    if config.DELISTING_PATH.exists():
        registry = pd.read_parquet(config.DELISTING_PATH)
        registry["code"] = registry["code"].astype(str).str.zfill(6)
        registry["delisting_date"] = pd.to_datetime(registry["delisting_date"], errors="coerce")
        after_delisting = clean.assign(code=clean["code"].astype(str).str.zfill(6)).merge(
            registry[["code", "delisting_date"]], on="code", how="inner"
        )
        invalid_post_delisting = int((after_delisting["date"] > after_delisting["delisting_date"]).sum())
        rows.append(
            _record(
                "post_delisting_prices",
                "pass" if invalid_post_delisting == 0 else "warning",
                invalid_post_delisting,
                "通过 AKShare 上深交易所终止上市清单检查退市后价格记录。",
            )
        )
    else:
        rows.append(
            _record(
                "post_delisting_prices",
                "warning",
                "metadata_missing",
                "运行 update_metadata.py --delisting 后可检查退市清单。",
            )
        )

    signal = pd.read_parquet(config.SIGNAL_PATH)
    signal["date"] = pd.to_datetime(signal["date"])
    signal_names = signal["name"].fillna("").astype(str)
    signal_st = int(signal_names.str.upper().str.contains("ST", regex=False).sum())
    rows.append(_record("signal_st_exclusion", "pass" if signal_st == 0 else "fail", signal_st, "调仓信号不应包含 ST"))
    if "listing_days" in clean:
        signal_listing = signal.merge(clean[["date", "code", "listing_days"]], on=["date", "code"], how="left")
        too_new = int((signal_listing["listing_days"] < config.MIN_LISTING_DAYS).sum())
        rows.append(_record("signal_listing_age", "pass" if too_new == 0 else "fail", too_new, "信号股票应满足上市天数限制"))
    else:
        rows.append(_record("signal_listing_age", "warning", "unavailable", "clean_daily 不包含 listing_days；由因子层计算。"))

    trades = pd.read_csv(config.TRADES_PATH, parse_dates=["signal_date", "execution_date"])
    invalid_execution = int((trades["execution_date"] <= trades["signal_date"]).sum())
    rows.append(_record("t_plus_one_execution", "pass" if invalid_execution == 0 else "fail", invalid_execution, "执行日必须严格晚于信号日"))

    factor = pd.read_parquet(config.FACTOR_DAILY_PATH, columns=["date", "code", "open"])
    factor["date"] = pd.to_datetime(factor["date"])
    execution_rows = signal.merge(
        trades[["signal_date", "execution_date"]], left_on="date", right_on="signal_date", how="inner"
    )[["execution_date", "code"]]
    execution_open = execution_rows.merge(
        factor.rename(columns={"date": "execution_date"}), on=["execution_date", "code"], how="left"
    )
    unavailable_mask = execution_open["open"].isna() | (execution_open["open"].fillna(0) <= 0)
    unavailable_open = int(unavailable_mask.sum())
    execution_issues = execution_open.loc[unavailable_mask].copy()
    execution_issues["code"] = execution_issues["code"].astype(str).str.zfill(6)
    execution_issues.to_csv(
        config.RESULT_DIR / "execution_open_issues.csv", index=False, encoding="utf-8-sig"
    )
    rows.append(
        _record(
            "execution_open_availability",
            "pass" if unavailable_open == 0 else "warning",
            unavailable_open,
            "问题明细已输出到 execution_open_issues.csv；涨跌停和停牌可成交性仍需订单级数据。",
        )
    )
    if config.SUSPENSION_PATH.exists():
        suspension = pd.read_parquet(config.SUSPENSION_PATH)
        suspension["requested_date"] = pd.to_datetime(suspension["requested_date"], errors="coerce")
        suspension["suspension_date"] = pd.to_datetime(suspension["suspension_date"], errors="coerce")
        suspension["resumption_date"] = pd.to_datetime(suspension["resumption_date"], errors="coerce")
        suspension["code"] = suspension["code"].astype(str).str.zfill(6)
        execution_suspension = execution_rows.assign(code=execution_rows["code"].astype(str).str.zfill(6)).merge(
            suspension[["code", "suspension_date", "resumption_date"]],
            on="code",
            how="inner",
        )
        requested_suspension = execution_suspension[
            (execution_suspension["suspension_date"] <= execution_suspension["execution_date"])
            & (
                execution_suspension["resumption_date"].isna()
                | (execution_suspension["resumption_date"] >= execution_suspension["execution_date"])
            )
        ]
        rows.append(
            _record(
                "suspension_execution_events",
                "pass" if requested_suspension.empty else "warning",
                len(requested_suspension),
                "命中停复牌缓存的执行日；需在回测中按未成交处理。",
            )
        )
    else:
        rows.append(
            _record(
                "suspension_execution_events",
                "warning",
                "metadata_missing",
                "运行 update_metadata.py --suspension-dates <执行日...> 后可检查。",
            )
        )
    rows.append(
        _record(
            "limit_up_down_execution",
            "warning",
            "unavailable",
            "当前日线数据没有订单簿和涨跌停可成交标识，不能确认涨跌停日的真实成交。",
        )
    )

    # Prefix-invariance check: past factor values must not change when future
    # rows are removed. A small deterministic stock sample keeps this check fast.
    sample_codes = sorted(clean["code"].astype(str).unique())[:20]
    sample = clean[clean["code"].astype(str).isin(sample_codes)].copy()
    cutoff = sample["date"].quantile(0.8)
    full_factors = build_factors(sample)
    prefix_factors = build_factors(sample[sample["date"] <= cutoff])
    columns = ["mom_120_20", "trend_120", "rev_10", "vol_60", "drawdown_120", "score"]
    joined = full_factors[full_factors["date"] <= cutoff][["date", "code", *columns]].merge(
        prefix_factors[["date", "code", *columns]], on=["date", "code"], suffixes=("_full", "_prefix")
    )
    differences = 0
    for column in columns:
        left = joined[f"{column}_full"].to_numpy(dtype=float)
        right = joined[f"{column}_prefix"].to_numpy(dtype=float)
        differences += int((~np.isclose(left, right, equal_nan=True)).sum())
    rows.append(
        _record(
            "future_data_prefix_invariance",
            "pass" if differences == 0 else "fail",
            differences,
            "截断未来数据后，截断点之前的因子和得分不应变化。",
        )
    )

    output = pd.DataFrame(rows)
    output.to_csv(config.RESULT_DIR / "data_integrity_checks.csv", index=False, encoding="utf-8-sig")
    summary = {"pass": int((output["status"] == "pass").sum()), "warning": int((output["status"] == "warning").sum()), "fail": int((output["status"] == "fail").sum())}
    (config.RESULT_DIR / "data_integrity_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
