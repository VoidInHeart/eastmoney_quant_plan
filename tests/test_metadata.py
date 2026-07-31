import pandas as pd

from eastmoney_quant import config, metadata
from eastmoney_quant.metadata import normalize_delisting, normalize_suspension


def test_normalize_delisting_combines_sse_and_szse_records():
    shanghai = pd.DataFrame(
        {
            "公司代码": ["600001"],
            "公司简称": ["测试沪股"],
            "上市日期": ["2000-01-01"],
            "暂停上市日期": ["2020-01-01"],
        }
    )
    shenzhen = pd.DataFrame(
        {
            "证券代码": ["1"],
            "证券简称": ["测试深股"],
            "上市日期": ["2001-01-01"],
            "终止上市日期": ["2021-01-01"],
        }
    )

    result = normalize_delisting(shanghai, shenzhen)

    assert result["code"].tolist() == ["600001", "000001"]
    assert result["exchange"].tolist() == ["SSE", "SZSE"]
    assert result["delisting_date"].notna().all()


def test_normalize_suspension_keeps_requested_date_and_zero_padded_code():
    raw = pd.DataFrame(
        {
            "代码": ["2848"],
            "名称": ["测试"],
            "停牌时间": ["2026-06-01"],
            "停牌截止时间": ["2026-06-02"],
            "停牌原因": ["测试原因"],
            "所属市场": ["深交所"],
            "预计复牌时间": ["2026-06-03"],
        }
    )

    result = normalize_suspension(raw, "20260601")

    assert result["code"].iloc[0] == "002848"
    assert result["requested_date"].iloc[0] == pd.Timestamp("2026-06-01")


def test_incremental_suspension_fetch_logs_empty_success_and_resumes(monkeypatch, tmp_path):
    class FakeAKShare:
        calls = 0

        def stock_tfp_em(self, date):
            self.calls += 1
            return pd.DataFrame(columns=["代码", "名称", "停牌时间", "停牌截止时间", "停牌原因", "所属市场", "预计复牌时间"])

    fake = FakeAKShare()
    monkeypatch.setattr(metadata, "_akshare", lambda: fake)
    monkeypatch.setattr(config, "SUSPENSION_PATH", tmp_path / "suspensions.parquet")
    monkeypatch.setattr(config, "SUSPENSION_FETCH_LOG_PATH", tmp_path / "suspension_log.parquet")

    first = metadata.fetch_suspensions_incremental(["20250102"], sleep_seconds=0)
    second = metadata.fetch_suspensions_incremental(["20250102"], sleep_seconds=0)

    assert first["status"].tolist() == ["success"]
    assert second["status"].tolist() == ["success"]
    assert fake.calls == 1


def test_incremental_cninfo_backfill_records_empty_response_as_complete(monkeypatch, tmp_path):
    class FakeAKShare:
        calls = 0

        def stock_industry_change_cninfo(self, **kwargs):
            self.calls += 1
            return pd.DataFrame()

    fake = FakeAKShare()
    monkeypatch.setattr(metadata, "_akshare", lambda: fake)
    monkeypatch.setattr(config, "INDUSTRY_CHANGE_PATH", tmp_path / "industry.parquet")
    monkeypatch.setattr(config, "INDUSTRY_FETCH_LOG_PATH", tmp_path / "industry_log.parquet")

    assert metadata.pending_cninfo_codes("industry", ["1"]) == ["000001"]
    log = metadata.fetch_cninfo_events_incremental(
        "industry", ["000001"], "20200101", "20201231", sleep_seconds=0
    )

    assert log["status"].tolist() == ["success"]
    assert metadata.pending_cninfo_codes("industry", ["000001"]) == []
    assert fake.calls == 1
