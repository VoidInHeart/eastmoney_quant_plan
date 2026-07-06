from __future__ import annotations

import logging
import os
import time
from typing import Iterable

import numpy as np
import pandas as pd
import requests

from . import config
from .data_clean import normalize_columns


LOGGER = logging.getLogger(__name__)
VALID_HISTORY_SOURCES = {"auto", "eastmoney", "tencent"}
_EASTMONEY_HISTORY_DISABLED = False

EASTMONEY_NO_PROXY_HOSTS = [
    "eastmoney.com",
    ".eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "82.push2.eastmoney.com",
]

SPOT_FIELDS = ",".join(
    [
        "f2",
        "f3",
        "f4",
        "f5",
        "f6",
        "f7",
        "f8",
        "f9",
        "f10",
        "f12",
        "f14",
        "f15",
        "f16",
        "f17",
        "f18",
        "f20",
        "f21",
        "f23",
    ]
)

SPOT_FIELD_MAP = {
    "f12": "code",
    "f14": "name",
    "f2": "latest_price",
    "f3": "pct_chg",
    "f4": "change",
    "f5": "volume",
    "f6": "amount",
    "f7": "amplitude",
    "f8": "turnover",
    "f9": "pe_dynamic",
    "f10": "volume_ratio",
    "f15": "high",
    "f16": "low",
    "f17": "open",
    "f18": "prev_close",
    "f20": "total_market_value",
    "f21": "float_market_value",
    "f23": "pb",
}

KLINE_COLUMNS = [
    "date",
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

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


def bypass_proxy_for_eastmoney() -> None:
    """Avoid sending Eastmoney market-data requests through a misconfigured proxy."""
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    hosts = [item.strip() for item in current.split(",") if item.strip()]
    for host in EASTMONEY_NO_PROXY_HOSTS:
        if host not in hosts:
            hosts.append(host)
    value = ",".join(hosts)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def _eastmoney_session() -> requests.Session:
    bypass_proxy_for_eastmoney()
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    session.trust_env = os.environ.get("EASTMONEY_USE_PROXY", "0") == "1"
    return session


def _request_eastmoney_json(url: str, params: dict) -> dict:
    session = _eastmoney_session()
    response = session.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def _to_secid(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _to_tx_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def fetch_stock_list_direct(page_size: int = 500) -> pd.DataFrame:
    """Fetch the A-share spot table directly from Eastmoney as an AKShare fallback."""
    urls = [
        "https://push2.eastmoney.com/api/qt/clist/get",
        "https://82.push2.eastmoney.com/api/qt/clist/get",
        "http://push2.eastmoney.com/api/qt/clist/get",
    ]
    params = {
        "pn": 1,
        "pz": page_size,
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": SPOT_FIELDS,
    }
    last_error: Exception | None = None
    for url in urls:
        frames: list[pd.DataFrame] = []
        try:
            page = 1
            total = None
            while True:
                params["pn"] = page
                payload = _request_eastmoney_json(url, params)
                data = payload.get("data") or {}
                rows = data.get("diff") or []
                if not rows:
                    break
                total = data.get("total", total)
                frames.append(pd.DataFrame(rows).rename(columns=SPOT_FIELD_MAP))
                if total is not None and page * page_size >= int(total):
                    break
                page += 1
            if frames:
                result = pd.concat(frames, ignore_index=True)
                result["code"] = result["code"].astype(str).str.zfill(6)
                return result
        except Exception as exc:
            last_error = exc
            LOGGER.info("direct Eastmoney stock list failed for %s: %s", url, exc)
    raise RuntimeError(f"failed to fetch Eastmoney stock list directly: {last_error}")


def fetch_stock_code_name_fallback() -> pd.DataFrame:
    """Fetch a lightweight A-share code/name table when the spot endpoint is unstable."""
    ak = _load_akshare()
    df = normalize_columns(ak.stock_info_a_code_name())
    if "code" not in df.columns:
        raise ValueError("fallback code/name table did not include a code column")
    if "name" not in df.columns:
        possible_name_cols = [col for col in df.columns if "name" in col.lower()]
        if possible_name_cols:
            df = df.rename(columns={possible_name_cols[0]: "name"})
    if "name" not in df.columns:
        df["name"] = ""
    return df[["code", "name"]].copy()


def fetch_one_stock_history_direct(
    code: str,
    start_date: str,
    end_date: str,
    adjust: str = "hfq",
) -> pd.DataFrame:
    """Fetch one stock's daily K-line directly from Eastmoney as an AKShare fallback."""
    fqt_map = {"": 0, "none": 0, "qfq": 1, "hfq": 2}
    params = {
        "secid": _to_secid(code),
        "klt": 101,
        "fqt": fqt_map.get(adjust, 2),
        "beg": start_date,
        "end": end_date,
        "lmt": 1_000_000,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    urls = [
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        "https://push2.eastmoney.com/api/qt/stock/kline/get",
        "http://push2his.eastmoney.com/api/qt/stock/kline/get",
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            payload = _request_eastmoney_json(url, params)
            data = payload.get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                return pd.DataFrame()
            rows = [line.split(",") for line in klines]
            df = pd.DataFrame(rows, columns=KLINE_COLUMNS)
            df["code"] = str(code).zfill(6)
            return df
        except Exception as exc:
            last_error = exc
            LOGGER.debug("direct Eastmoney kline failed for %s %s: %s", code, url, exc)
    raise RuntimeError(f"failed to fetch Eastmoney kline directly for {code}: {last_error}")


def fetch_one_stock_history_tx(
    code: str,
    start_date: str,
    end_date: str,
    adjust: str = "hfq",
) -> pd.DataFrame:
    """Fetch one stock's daily history from Tencent through AKShare as a final fallback."""
    ak = _load_akshare()
    code = str(code).zfill(6)
    df = ak.stock_zh_a_hist_tx(
        symbol=_to_tx_symbol(code),
        start_date=start_date,
        end_date=end_date,
        adjust=adjust if adjust in {"qfq", "hfq", ""} else "",
        timeout=15,
    )
    if df.empty:
        return df
    df = df.copy()
    df["code"] = code
    if "volume" not in df.columns and "amount" in df.columns:
        df["volume"] = pd.to_numeric(df["amount"], errors="coerce")
    if "amount" in df.columns:
        amount = pd.to_numeric(df["amount"], errors="coerce")
        close = pd.to_numeric(df["close"], errors="coerce")
        df["amount"] = amount * close
    return df


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("AKShare is not installed. Run `pip install -r requirements.txt`.") from exc
    return ak


def fetch_stock_list(prefer_code_name: bool = False) -> pd.DataFrame:
    """Fetch the latest A-share spot table from Eastmoney through AKShare."""
    bypass_proxy_for_eastmoney()
    if prefer_code_name:
        spot = fetch_stock_code_name_fallback()
    else:
        try:
            ak = _load_akshare()
            spot = normalize_columns(ak.stock_zh_a_spot_em())
        except Exception as exc:
            LOGGER.info("Eastmoney spot list is unavailable; trying direct API. Reason: %s", exc)
            try:
                spot = fetch_stock_list_direct()
            except Exception as direct_exc:
                LOGGER.info("Eastmoney direct spot list is unavailable; using code/name table. Reason: %s", direct_exc)
                spot = fetch_stock_code_name_fallback()
    if "code" not in spot.columns:
        raise ValueError("AKShare spot data did not include a stock code column")
    spot["code"] = spot["code"].astype(str).str.zfill(6)
    if "name" not in spot.columns:
        spot["name"] = ""
    for col in spot.columns:
        if col not in {"code", "name"}:
            spot[col] = pd.to_numeric(spot[col].replace("-", pd.NA), errors="coerce")
    config.ensure_directories()
    spot.to_parquet(config.SPOT_CACHE_PATH, index=False)
    LOGGER.info("saved stock list to %s, rows=%s", config.SPOT_CACHE_PATH, len(spot))
    return spot


def _fetch_one_stock_history_eastmoney(
    code: str,
    start_date: str,
    end_date: str,
    adjust: str = "hfq",
) -> pd.DataFrame:
    """Fetch one stock's daily history from Eastmoney through AKShare/direct API."""
    try:
        ak = _load_akshare()
        return ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    except Exception as exc:
        LOGGER.debug("AKShare Eastmoney history failed for %s, trying direct API: %s", code, exc)
        return fetch_one_stock_history_direct(code, start_date, end_date, adjust)


def fetch_one_stock_history(
    code: str,
    start_date: str,
    end_date: str,
    adjust: str = "hfq",
    source: str = config.HISTORY_SOURCE,
) -> pd.DataFrame:
    """Fetch one stock's daily history, with adaptive source fallback."""
    global _EASTMONEY_HISTORY_DISABLED

    bypass_proxy_for_eastmoney()
    source = source.lower()
    if source not in VALID_HISTORY_SOURCES:
        raise ValueError(f"invalid history source: {source}, choose from {sorted(VALID_HISTORY_SOURCES)}")

    code = str(code).zfill(6)
    if source == "tencent":
        df = fetch_one_stock_history_tx(code, start_date, end_date, adjust)
    elif source == "eastmoney":
        df = _fetch_one_stock_history_eastmoney(code, start_date, end_date, adjust)
    elif _EASTMONEY_HISTORY_DISABLED:
        df = fetch_one_stock_history_tx(code, start_date, end_date, adjust)
    else:
        try:
            df = _fetch_one_stock_history_eastmoney(code, start_date, end_date, adjust)
        except Exception as exc:
            _EASTMONEY_HISTORY_DISABLED = True
            LOGGER.info(
                "Eastmoney history source is unavailable; using Tencent for the remaining stocks. Reason: %s",
                exc,
            )
            df = fetch_one_stock_history_tx(code, start_date, end_date, adjust)
    if df.empty:
        return df
    df["code"] = code
    return df


def _iter_codes(stock_list: pd.DataFrame | None, codes: Iterable[str] | None) -> list[str]:
    if codes is not None:
        return [str(code).zfill(6) for code in codes]
    if stock_list is None:
        stock_list = fetch_stock_list()
    return stock_list["code"].astype(str).str.zfill(6).drop_duplicates().tolist()


def fetch_all_stock_history(
    start_date: str,
    end_date: str,
    adjust: str = "hfq",
    codes: Iterable[str] | None = None,
    limit: int | None = None,
    history_source: str = config.HISTORY_SOURCE,
    sleep_seconds: float = config.FETCH_SLEEP_SECONDS,
    force: bool = False,
) -> pd.DataFrame:
    """Batch fetch daily history, with local parquet cache and polite throttling."""
    config.ensure_directories()
    if config.RAW_DAILY_PATH.exists() and not force:
        LOGGER.info("loading raw daily cache from %s", config.RAW_DAILY_PATH)
        return pd.read_parquet(config.RAW_DAILY_PATH)

    history_source = history_source.lower()
    stock_list = fetch_stock_list(prefer_code_name=history_source == "tencent")
    if codes is None and "name" in stock_list.columns:
        name = stock_list["name"].fillna("").astype(str)
        stock_list = stock_list.loc[~name.str.contains("退", regex=False)].copy()
    name_map = stock_list.set_index("code")["name"].to_dict() if "name" in stock_list else {}
    frames: list[pd.DataFrame] = []
    target_codes = _iter_codes(stock_list, codes)
    if limit is not None:
        target_codes = target_codes[:limit]

    for index, code in enumerate(target_codes, start=1):
        last_error: Exception | None = None
        for attempt in range(1, config.MAX_FETCH_RETRY + 1):
            try:
                df = fetch_one_stock_history(code, start_date, end_date, adjust, source=history_source)
                if not df.empty:
                    df["name"] = name_map.get(code, "")
                    frames.append(df)
                break
            except Exception as exc:  # pragma: no cover - network/API instability
                last_error = exc
                LOGGER.warning("fetch failed for %s attempt %s: %s", code, attempt, exc)
                time.sleep(sleep_seconds * attempt)
        if last_error and attempt == config.MAX_FETCH_RETRY:
            LOGGER.error("skip %s after retries: %s", code, last_error)
        if index % 50 == 0:
            LOGGER.info("fetched %s/%s stocks", index, len(target_codes))
        time.sleep(sleep_seconds)

    if not frames:
        raise RuntimeError("no stock history was fetched")

    result = pd.concat(frames, ignore_index=True)
    result.to_parquet(config.RAW_DAILY_PATH, index=False)
    LOGGER.info("saved raw daily data to %s, rows=%s", config.RAW_DAILY_PATH, len(result))
    return result


def make_sample_data(
    codes: int = 30,
    start_date: str = "20200101",
    end_date: str = "20211231",
    seed: int = 7,
) -> pd.DataFrame:
    """Build deterministic sample data so the pipeline can be tested offline."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(pd.to_datetime(start_date), pd.to_datetime(end_date))
    frames: list[pd.DataFrame] = []
    for i in range(codes):
        code = f"{i + 1:06d}"
        name = f"Sample{i + 1:03d}"
        drift = rng.normal(0.0002, 0.0001)
        ret = rng.normal(drift, 0.018, len(dates))
        close = 10 * np.cumprod(1 + ret)
        open_ = close * (1 + rng.normal(0, 0.004, len(dates)))
        high = np.maximum(open_, close) * (1 + rng.random(len(dates)) * 0.02)
        low = np.minimum(open_, close) * (1 - rng.random(len(dates)) * 0.02)
        amount = rng.uniform(1.2e8, 6e8, len(dates))
        volume = amount / close * 100
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "code": code,
                    "name": name,
                    "open": open_,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "amount": amount,
                    "amplitude": (high / low - 1) * 100,
                    "pct_chg": pd.Series(close).pct_change().fillna(0).to_numpy() * 100,
                    "change": pd.Series(close).diff().fillna(0).to_numpy(),
                    "turnover": rng.uniform(1, 8, len(dates)),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)
