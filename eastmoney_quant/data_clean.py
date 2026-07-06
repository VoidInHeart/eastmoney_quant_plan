from __future__ import annotations

import logging

import pandas as pd

from . import config


LOGGER = logging.getLogger(__name__)

COLUMN_MAP = {
    "日期": "date",
    "股票代码": "code",
    "代码": "code",
    "股票简称": "name",
    "名称": "name",
    "开盘": "open",
    "今开": "open",
    "收盘": "close",
    "最新价": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_chg",
    "涨跌额": "change",
    "换手率": "turnover",
}

STANDARD_COLUMNS = [
    "date",
    "code",
    "name",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "change",
    "turnover",
]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename AKShare's Chinese fields to stable English field names."""
    result = df.copy()
    result = result.rename(columns={col: COLUMN_MAP.get(col, col) for col in result.columns})
    result = result.loc[:, ~result.columns.duplicated(keep="last")]
    return result


def clean_daily_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean daily A-share price data into the schema used by the strategy."""
    if df.empty:
        raise ValueError("daily data is empty")

    result = normalize_columns(df)
    required = {"date", "code", "open", "close", "high", "low"}
    missing = required - set(result.columns)
    if missing:
        raise ValueError(f"daily data missing required columns: {sorted(missing)}")

    if "name" not in result.columns:
        result["name"] = ""

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["code"] = result["code"].astype(str).str.extract(r"(\d{1,6})", expand=False).str.zfill(6)
    result["name"] = result["name"].fillna("").astype(str)

    numeric_cols = [col for col in STANDARD_COLUMNS if col not in {"date", "code", "name"}]
    for col in numeric_cols:
        if col not in result.columns:
            result[col] = pd.NA
        result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result.dropna(subset=["date", "code", "open", "close", "high", "low"])
    result = result[result["close"] > 0]
    result = result.drop_duplicates(subset=["code", "date"], keep="last")
    result = result.sort_values(["code", "date"]).reset_index(drop=True)
    return result[STANDARD_COLUMNS]


def save_clean_data(df: pd.DataFrame) -> None:
    """Persist cleaned data to the configured parquet cache."""
    config.ensure_directories()
    df.to_parquet(config.CLEAN_DAILY_PATH, index=False)
    LOGGER.info("saved clean data to %s, rows=%s", config.CLEAN_DAILY_PATH, len(df))
