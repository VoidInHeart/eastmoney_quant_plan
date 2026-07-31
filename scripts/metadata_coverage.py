"""Report whether cached metadata is sufficient for strict historical research."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eastmoney_quant import config


def _codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(pd.read_parquet(path, columns=["code"])["code"].astype(str).str.zfill(6))


def _completed_codes(data_path: Path, log_path: Path) -> set[str]:
    completed = _codes(data_path)
    if log_path.exists():
        log = pd.read_parquet(log_path, columns=["code", "status"])
        completed |= set(log.loc[log["status"].eq("success"), "code"].astype(str).str.zfill(6))
    return completed


def main() -> None:
    clean = pd.read_parquet(config.CLEAN_DAILY_PATH, columns=["date", "code"])
    trade_dates = pd.to_datetime(clean["date"]).dropna().drop_duplicates()
    all_codes = clean["code"].astype(str).str.zfill(6).nunique()
    success_dates = pd.Series(dtype="datetime64[ns]")
    failed_dates = pd.Series(dtype="datetime64[ns]")
    if config.SUSPENSION_FETCH_LOG_PATH.exists():
        log = pd.read_parquet(config.SUSPENSION_FETCH_LOG_PATH)
        log["requested_date"] = pd.to_datetime(log["requested_date"], errors="coerce")
        success_dates = log.loc[log["status"].eq("success"), "requested_date"].dropna().drop_duplicates()
        failed_dates = log.loc[log["status"].eq("failed"), "requested_date"].dropna().drop_duplicates()
    event_dates = pd.Series(dtype="datetime64[ns]")
    if config.SUSPENSION_PATH.exists():
        event_dates = pd.to_datetime(pd.read_parquet(config.SUSPENSION_PATH, columns=["requested_date"])["requested_date"], errors="coerce").dropna().drop_duplicates()
    completed_dates = pd.Series(list(set(success_dates) | set(event_dates)), dtype="datetime64[ns]")
    summary = {
        "trade_dates": int(len(trade_dates)),
        "universe_codes": int(all_codes),
        "suspension_success_dates": int(len(completed_dates)),
        "suspension_logged_success_dates": int(len(success_dates)),
        "suspension_event_dates": int(len(event_dates)),
        "suspension_failed_dates": int(len(failed_dates)),
        "suspension_missing_dates": int(len(set(trade_dates) - set(completed_dates))),
        "industry_change_codes": len(_codes(config.INDUSTRY_CHANGE_PATH)),
        "share_change_codes": len(_codes(config.SHARE_CHANGE_PATH)),
        "industry_completed_codes": len(_completed_codes(config.INDUSTRY_CHANGE_PATH, config.INDUSTRY_FETCH_LOG_PATH)),
        "share_completed_codes": len(_completed_codes(config.SHARE_CHANGE_PATH, config.SHARE_FETCH_LOG_PATH)),
        "delisting_registry_rows": int(len(pd.read_parquet(config.DELISTING_PATH))) if config.DELISTING_PATH.exists() else 0,
    }
    summary["ready_for_point_in_time_v2"] = bool(
        summary["suspension_missing_dates"] == 0
        and summary["industry_completed_codes"] >= summary["universe_codes"]
        and summary["share_completed_codes"] >= summary["universe_codes"]
    )
    summary["warning"] = (
        "覆盖率不足时，严格执行与行业/市值中性化只能用于研究诊断，不能作为样本外策略结论。"
        if not summary["ready_for_point_in_time_v2"]
        else "元数据覆盖已满足当前点时研究的最低检查。"
    )
    output = config.RESULT_DIR / "metadata_coverage.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
