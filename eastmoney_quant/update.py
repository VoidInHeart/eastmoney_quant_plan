from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from . import config
from .backtest import run_backtest
from .data_clean import clean_daily_data, save_clean_data
from .data_fetch import fetch_one_stock_history
from .factor import build_factors
from .prediction import run_prediction
from .report import export_report
from .signal import build_rebalance_signal


LOGGER = logging.getLogger(__name__)


@dataclass
class SafeUpdateResult:
    old_rows: int
    new_rows: int
    merged_rows: int
    old_latest_date: pd.Timestamp
    merged_latest_date: pd.Timestamp
    updated_codes: int
    skipped_codes: int
    backup_path: Path | None
    raw_path: Path


def _timestamp() -> str:
    return pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")


def backup_path_for(path: Path) -> Path:
    backup_dir = path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"{path.stem}_{_timestamp()}{path.suffix}"


def safe_replace_parquet(path: Path, data: pd.DataFrame, backup: bool = True) -> Path | None:
    """Write a parquet file through a temp file, then atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.stem}.tmp_{_timestamp()}{path.suffix}")
    data.to_parquet(tmp_path, index=False)

    backup_path = None
    if path.exists() and backup:
        backup_path = backup_path_for(path)
        shutil.copy2(path, backup_path)

    os.replace(tmp_path, path)
    return backup_path


def _ensure_date_code(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result = result.loc[:, ~result.columns.duplicated(keep="last")]
    rename_map = {}
    if "date" not in result.columns and "日期" in result.columns:
        rename_map["日期"] = "date"
    if "code" not in result.columns and "股票代码" in result.columns:
        rename_map["股票代码"] = "code"
    if rename_map:
        result = result.rename(columns=rename_map)
    if "date" not in result.columns or "code" not in result.columns:
        raise ValueError("raw data must include date/code columns")
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["code"] = result["code"].astype(str).str.extract(r"(\d{1,6})", expand=False).str.zfill(6)
    result = result.dropna(subset=["date", "code"])
    return result


def merge_raw_data(old_raw: pd.DataFrame, new_raw: pd.DataFrame) -> pd.DataFrame:
    """Merge old and incremental raw data, keeping new rows on duplicated code/date."""
    old = _ensure_date_code(old_raw)
    new = _ensure_date_code(new_raw)
    old_unique_rows = len(old.drop_duplicates(subset=["code", "date"], keep="last"))
    all_columns = list(dict.fromkeys([*old.columns, *new.columns]))
    merged = pd.concat([old.reindex(columns=all_columns), new.reindex(columns=all_columns)], ignore_index=True)
    merged = _ensure_date_code(merged)
    merged = merged.sort_values(["code", "date"]).drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged.reset_index(drop=True)
    if len(merged) < old_unique_rows:
        raise ValueError("merged data has fewer unique rows than existing cache; aborting replace")
    if merged["date"].max() < old["date"].max():
        raise ValueError("merged data latest date is older than existing cache; aborting replace")
    return merged


def infer_update_window(raw: pd.DataFrame, lookback_days: int, end_date: str | None = None) -> tuple[str, str]:
    prepared = _ensure_date_code(raw)
    latest = prepared["date"].max()
    start = latest - pd.Timedelta(days=lookback_days)
    end = pd.Timestamp.today() if end_date is None else pd.to_datetime(end_date)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _name_map(raw: pd.DataFrame) -> dict[str, str]:
    if "name" not in raw.columns:
        return {}
    prepared = _ensure_date_code(raw)
    prepared["name"] = prepared["name"].fillna("").astype(str)
    latest_names = prepared.sort_values("date").groupby("code").tail(1)
    return dict(zip(latest_names["code"], latest_names["name"]))


def fetch_incremental_history(
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    history_source: str,
    adjust: str = config.ADJUST,
    name_map: dict[str, str] | None = None,
    sleep_seconds: float = config.FETCH_SLEEP_SECONDS,
    max_retry: int = config.MAX_FETCH_RETRY,
) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    name_map = name_map or {}
    codes = [str(code).zfill(6) for code in codes]
    for index, code in enumerate(codes, start=1):
        last_error: Exception | None = None
        for attempt in range(1, max_retry + 1):
            try:
                df = fetch_one_stock_history(
                    code=code,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                    source=history_source,
                )
                if not df.empty:
                    df = df.copy()
                    df["code"] = code
                    if "name" not in df.columns:
                        df["name"] = name_map.get(code, "")
                    frames.append(df)
                break
            except Exception as exc:  # pragma: no cover - network/API instability
                last_error = exc
                LOGGER.warning("update fetch failed for %s attempt %s: %s", code, attempt, exc)
                time.sleep(sleep_seconds * attempt)
        else:
            skipped.append(code)
            LOGGER.error("skip %s after retries: %s", code, last_error)

        if index % 50 == 0:
            LOGGER.info("updated %s/%s codes", index, len(codes))
        time.sleep(sleep_seconds)

    if not frames:
        return pd.DataFrame(), skipped
    return pd.concat(frames, ignore_index=True), skipped


def rebuild_outputs(raw_data: pd.DataFrame, top_n: int = config.TOP_N, run_predict: bool = True) -> None:
    clean_data = clean_daily_data(raw_data)
    save_clean_data(clean_data)

    factor_data = build_factors(clean_data)
    factor_data.to_parquet(config.FACTOR_DAILY_PATH, index=False)

    signal = build_rebalance_signal(factor_data, top_n=top_n)
    signal.to_parquet(config.SIGNAL_PATH, index=False)

    result = run_backtest(factor_data, signal)
    export_report(result)

    if run_predict:
        run_prediction(top_n=top_n, refresh_factors=False, append_log=True)


def safe_incremental_update(
    raw_path: Path = config.RAW_DAILY_PATH,
    lookback_days: int = 15,
    end_date: str | None = None,
    history_source: str = "tencent",
    codes: Iterable[str] | None = None,
    limit: int | None = None,
    backup: bool = True,
    rebuild: bool = True,
    run_predict: bool = True,
    top_n: int = config.TOP_N,
) -> SafeUpdateResult:
    """Safely append recent history into the existing raw cache."""
    if not raw_path.exists():
        raise FileNotFoundError(f"raw cache not found: {raw_path}")

    old_raw = pd.read_parquet(raw_path)
    old_prepared = _ensure_date_code(old_raw)
    start_date, final_end_date = infer_update_window(old_prepared, lookback_days, end_date=end_date)
    target_codes = [str(code).zfill(6) for code in codes] if codes is not None else sorted(old_prepared["code"].unique())
    if limit is not None:
        target_codes = target_codes[:limit]

    LOGGER.info(
        "safe update window: %s -> %s, codes=%s, source=%s",
        start_date,
        final_end_date,
        len(target_codes),
        history_source,
    )
    new_raw, skipped = fetch_incremental_history(
        codes=target_codes,
        start_date=start_date,
        end_date=final_end_date,
        history_source=history_source,
        name_map=_name_map(old_raw),
    )
    if new_raw.empty:
        raise RuntimeError("no incremental data was fetched; raw cache was not changed")

    merged = merge_raw_data(old_raw, new_raw)
    backup_path = safe_replace_parquet(raw_path, merged, backup=backup)
    LOGGER.info("raw cache safely replaced: %s rows -> %s rows", len(old_raw), len(merged))
    if backup_path is not None:
        LOGGER.info("backup saved to %s", backup_path)

    if rebuild:
        rebuild_outputs(merged, top_n=top_n, run_predict=run_predict)

    result = SafeUpdateResult(
        old_rows=len(old_raw),
        new_rows=len(new_raw),
        merged_rows=len(merged),
        old_latest_date=old_prepared["date"].max(),
        merged_latest_date=merged["date"].max(),
        updated_codes=len(set(new_raw["code"].astype(str).str.zfill(6))),
        skipped_codes=len(skipped),
        backup_path=backup_path,
        raw_path=raw_path,
    )
    summary_path = config.RESULT_DIR / "safe_update_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "old_rows": result.old_rows,
                "new_rows": result.new_rows,
                "merged_rows": result.merged_rows,
                "old_latest_date": result.old_latest_date.date().isoformat(),
                "merged_latest_date": result.merged_latest_date.date().isoformat(),
                "updated_codes": result.updated_codes,
                "skipped_codes": result.skipped_codes,
                "backup_path": str(result.backup_path) if result.backup_path else None,
                "raw_path": str(result.raw_path),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return result
