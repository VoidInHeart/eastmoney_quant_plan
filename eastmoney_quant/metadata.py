from __future__ import annotations

import time
from collections.abc import Iterable

import pandas as pd

from . import config


def _akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is optional at import time
        raise RuntimeError("AKShare is required for metadata enrichment") from exc
    return ak


def normalize_code(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"(\d{1,6})", expand=False).str.zfill(6)


def _upsert(path, data: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    config.ensure_directories()
    if path.exists():
        old = pd.read_parquet(path)
        columns = list(dict.fromkeys([*old.columns, *data.columns]))
        data = pd.concat([old.reindex(columns=columns), data.reindex(columns=columns)], ignore_index=True)
    data = data.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)
    data.to_parquet(path, index=False)
    return data


def normalize_delisting(shanghai: pd.DataFrame, shenzhen: pd.DataFrame) -> pd.DataFrame:
    sh = shanghai.rename(
        columns={"公司代码": "code", "公司简称": "name", "上市日期": "listing_date", "暂停上市日期": "delisting_date"}
    ).copy()
    sh["exchange"] = "SSE"
    sz = shenzhen.rename(
        columns={"证券代码": "code", "证券简称": "name", "上市日期": "listing_date", "终止上市日期": "delisting_date"}
    ).copy()
    sz["exchange"] = "SZSE"
    result = pd.concat([sh, sz], ignore_index=True)
    result["code"] = normalize_code(result["code"])
    for column in ("listing_date", "delisting_date"):
        result[column] = pd.to_datetime(result[column], errors="coerce")
    result["name"] = result["name"].fillna("").astype(str)
    result["status"] = "delisted_or_suspended"
    return result[["code", "name", "exchange", "listing_date", "delisting_date", "status"]]


def fetch_delisting_registry() -> pd.DataFrame:
    """Fetch official SSE/SZSE delisting registries through AKShare."""
    ak = _akshare()
    result = normalize_delisting(
        ak.stock_info_sh_delist(symbol="全部"),
        ak.stock_info_sz_delist(symbol="终止上市公司"),
    )
    return _upsert(config.DELISTING_PATH, result, ["code", "exchange", "delisting_date"])


def normalize_suspension(data: pd.DataFrame, requested_date: str) -> pd.DataFrame:
    result = data.rename(
        columns={
            "代码": "code",
            "名称": "name",
            "停牌时间": "suspension_date",
            "停牌截止时间": "resumption_date",
            "停牌原因": "reason",
            "所属市场": "market",
            "预计复牌时间": "expected_resumption_date",
        }
    ).copy()
    if result.empty:
        return pd.DataFrame(
            columns=["requested_date", "code", "name", "suspension_date", "resumption_date", "reason", "market", "expected_resumption_date"]
        )
    result["requested_date"] = pd.to_datetime(requested_date)
    result["code"] = normalize_code(result["code"])
    for column in ("suspension_date", "resumption_date", "expected_resumption_date"):
        result[column] = pd.to_datetime(result[column], errors="coerce")
    return result[
        ["requested_date", "code", "name", "suspension_date", "resumption_date", "reason", "market", "expected_resumption_date"]
    ]


def fetch_suspensions(dates: Iterable[str], sleep_seconds: float = 0.2) -> pd.DataFrame:
    """Fetch selected daily suspension records; callers choose dates explicitly."""
    ak = _akshare()
    frames = []
    for date in dates:
        date_text = pd.Timestamp(date).strftime("%Y%m%d")
        frames.append(normalize_suspension(ak.stock_tfp_em(date=date_text), date_text))
        time.sleep(sleep_seconds)
    result = pd.concat(frames, ignore_index=True) if frames else normalize_suspension(pd.DataFrame(), "19700101")
    return _upsert(config.SUSPENSION_PATH, result, ["requested_date", "code", "suspension_date"])


def fetch_suspensions_incremental(
    dates: Iterable[str],
    sleep_seconds: float = 0.2,
    retry_failed: bool = False,
    commit_every: int = 20,
) -> pd.DataFrame:
    """Fetch daily suspension snapshots one date at a time with a resume log.

    Successful dates with no suspended stocks are still recorded.  This makes a
    later coverage check meaningful and lets long historical backfills resume
    without re-requesting completed dates.
    """
    config.ensure_directories()
    columns = ["requested_date", "status", "row_count", "error", "fetched_at"]
    if config.SUSPENSION_FETCH_LOG_PATH.exists():
        existing = pd.read_parquet(config.SUSPENSION_FETCH_LOG_PATH).reindex(columns=columns)
    else:
        existing = pd.DataFrame(columns=columns)
    existing["requested_date"] = pd.to_datetime(existing["requested_date"], errors="coerce")
    completed = set(
        existing.loc[
            existing["status"].isin(["success", "imported_legacy"]), "requested_date"
        ].dropna()
    )
    attempted = set(existing["requested_date"].dropna())
    ak = _akshare()
    if commit_every < 1:
        raise ValueError("commit_every must be at least 1")
    rows: list[dict[str, object]] = []
    suspension_batches: list[pd.DataFrame] = []

    def commit_batch() -> None:
        nonlocal rows, suspension_batches
        if suspension_batches:
            _upsert(
                config.SUSPENSION_PATH,
                pd.concat(suspension_batches, ignore_index=True),
                ["requested_date", "code", "suspension_date"],
            )
        if rows:
            _upsert(config.SUSPENSION_FETCH_LOG_PATH, pd.DataFrame(rows, columns=columns), ["requested_date"])
        rows = []
        suspension_batches = []

    processed = 0
    for requested in dates:
        requested_date = pd.Timestamp(requested).normalize()
        if requested_date in completed or (not retry_failed and requested_date in attempted):
            continue
        try:
            data = normalize_suspension(ak.stock_tfp_em(date=requested_date.strftime("%Y%m%d")), requested_date.strftime("%Y%m%d"))
            if not data.empty:
                suspension_batches.append(data)
            rows.append(
                {
                    "requested_date": requested_date,
                    "status": "success",
                    "row_count": int(len(data)),
                    "error": "",
                    "fetched_at": pd.Timestamp.now(),
                }
            )
        except Exception as exc:  # pragma: no cover - relies on external service behaviour
            rows.append(
                {
                    "requested_date": requested_date,
                    "status": "failed",
                    "row_count": 0,
                    "error": str(exc)[:500],
                    "fetched_at": pd.Timestamp.now(),
                }
            )
        time.sleep(sleep_seconds)
        processed += 1
        if processed % commit_every == 0:
            commit_batch()
    commit_batch()
    if not config.SUSPENSION_FETCH_LOG_PATH.exists():
        return existing.sort_values("requested_date").reset_index(drop=True)
    return pd.read_parquet(config.SUSPENSION_FETCH_LOG_PATH).sort_values("requested_date").reset_index(drop=True)


def normalize_industry_changes(data: pd.DataFrame) -> pd.DataFrame:
    result = data.rename(
        columns={
            "证券代码": "code",
            "变更日期": "effective_date",
            "行业门类": "industry_sector",
            "行业大类": "industry_major",
            "行业中类": "industry_middle",
            "行业次类": "industry_minor",
            "行业编码": "industry_code",
            "分类标准": "classification",
            "分类标准编码": "classification_code",
        }
    ).copy()
    result["code"] = normalize_code(result["code"])
    result["effective_date"] = pd.to_datetime(result["effective_date"], errors="coerce")
    return result


def fetch_industry_changes(
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
    """Fetch point-in-time CNInfo industry-change events for explicitly supplied codes."""
    ak = _akshare()
    frames = []
    for code in codes:
        frame = ak.stock_industry_change_cninfo(
            symbol=str(code).zfill(6),
            start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
        )
        if not frame.empty:
            frames.append(normalize_industry_changes(frame))
        time.sleep(sleep_seconds)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    return _upsert(config.INDUSTRY_CHANGE_PATH, result, ["code", "effective_date", "classification_code", "industry_code"])


def normalize_share_changes(data: pd.DataFrame) -> pd.DataFrame:
    result = data.rename(
        columns={
            "证券代码": "code",
            "变动日期": "effective_date",
            "公告日期": "announcement_date",
            "总股本": "total_shares",
            "已流通股份": "float_shares",
            "变动原因": "change_reason",
        }
    ).copy()
    result["code"] = normalize_code(result["code"])
    for column in ("effective_date", "announcement_date"):
        result[column] = pd.to_datetime(result[column], errors="coerce")
    for column in ("total_shares", "float_shares"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def fetch_share_changes(
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
    """Fetch historical share-change events used to derive point-in-time market cap."""
    ak = _akshare()
    frames = []
    for code in codes:
        frame = ak.stock_share_change_cninfo(
            symbol=str(code).zfill(6),
            start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
        )
        if not frame.empty:
            frames.append(normalize_share_changes(frame))
        time.sleep(sleep_seconds)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    return _upsert(config.SHARE_CHANGE_PATH, result, ["code", "effective_date", "announcement_date"])


def _cninfo_paths(kind: str):
    if kind == "industry":
        return (
            config.INDUSTRY_CHANGE_PATH,
            config.INDUSTRY_FETCH_LOG_PATH,
            ["code", "effective_date", "classification_code", "industry_code"],
        )
    if kind == "share":
        return (
            config.SHARE_CHANGE_PATH,
            config.SHARE_FETCH_LOG_PATH,
            ["code", "effective_date", "announcement_date"],
        )
    raise ValueError("kind must be 'industry' or 'share'")


def _cninfo_fetch_state(kind: str) -> tuple[set[str], set[str]]:
    """Return completed and attempted codes for a resumable CNInfo backfill."""
    data_path, log_path, _ = _cninfo_paths(kind)
    completed: set[str] = set()
    attempted: set[str] = set()
    if data_path.exists():
        completed |= set(pd.read_parquet(data_path, columns=["code"])["code"].astype(str).str.zfill(6))
    if log_path.exists():
        log = pd.read_parquet(log_path, columns=["code", "status"])
        log["code"] = log["code"].astype(str).str.zfill(6)
        attempted |= set(log["code"])
        completed |= set(log.loc[log["status"].eq("success"), "code"])
    return completed, attempted


def pending_cninfo_codes(kind: str, codes: Iterable[str], retry_failed: bool = False) -> list[str]:
    """List source codes that have no successful event fetch yet."""
    completed, attempted = _cninfo_fetch_state(kind)
    normalized = sorted({str(code).zfill(6) for code in codes})
    return [code for code in normalized if code not in completed and (retry_failed or code not in attempted)]


def fetch_cninfo_events_incremental(
    kind: str,
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    sleep_seconds: float = 0.5,
    retry_failed: bool = False,
    commit_every: int = 5,
) -> pd.DataFrame:
    """Fetch a small code batch, recording successful empty responses as done.

    The caller should pass only the next missing codes from
    :func:`pending_cninfo_codes`.  Data and progress are persisted in small
    batches so interrupted long runs can resume safely.
    """
    if commit_every < 1:
        raise ValueError("commit_every must be at least 1")
    data_path, log_path, keys = _cninfo_paths(kind)
    completed, attempted = _cninfo_fetch_state(kind)
    requested_codes = [str(code).zfill(6) for code in codes]
    ak = _akshare()
    columns = ["code", "status", "row_count", "error", "start_date", "end_date", "fetched_at"]
    logs: list[dict[str, object]] = []
    event_batches: list[pd.DataFrame] = []

    def commit_batch() -> None:
        nonlocal logs, event_batches
        if event_batches:
            _upsert(data_path, pd.concat(event_batches, ignore_index=True), keys)
        if logs:
            _upsert(log_path, pd.DataFrame(logs, columns=columns), ["code"])
            print(f"{kind} metadata: persisted {processed} requested codes", flush=True)
        logs = []
        event_batches = []

    processed = 0
    for code in requested_codes:
        if code in completed or (not retry_failed and code in attempted):
            continue
        try:
            if kind == "industry":
                raw = ak.stock_industry_change_cninfo(
                    symbol=code,
                    start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
                    end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
                )
                event_data = normalize_industry_changes(raw) if not raw.empty else pd.DataFrame()
            else:
                raw = ak.stock_share_change_cninfo(
                    symbol=code,
                    start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
                    end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
                )
                event_data = normalize_share_changes(raw) if not raw.empty else pd.DataFrame()
            if not event_data.empty:
                event_batches.append(event_data)
            logs.append(
                {
                    "code": code,
                    "status": "success",
                    "row_count": int(len(event_data)),
                    "error": "",
                    "start_date": pd.Timestamp(start_date),
                    "end_date": pd.Timestamp(end_date),
                    "fetched_at": pd.Timestamp.now(),
                }
            )
        except Exception as exc:  # pragma: no cover - depends on remote CNInfo service
            logs.append(
                {
                    "code": code,
                    "status": "failed",
                    "row_count": 0,
                    "error": str(exc)[:500],
                    "start_date": pd.Timestamp(start_date),
                    "end_date": pd.Timestamp(end_date),
                    "fetched_at": pd.Timestamp.now(),
                }
            )
        time.sleep(sleep_seconds)
        processed += 1
        if processed % commit_every == 0:
            commit_batch()
    commit_batch()
    if not log_path.exists():
        return pd.DataFrame(columns=columns)
    return pd.read_parquet(log_path).sort_values("code").reset_index(drop=True)
