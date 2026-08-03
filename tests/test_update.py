import pandas as pd

from eastmoney_quant.update import merge_raw_data


def test_merge_raw_data_keeps_old_rows_and_overwrites_duplicates():
    old = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02", "2026-01-01"],
            "code": ["000001", "000001", "000002"],
            "close": [10.0, 11.0, 20.0],
        }
    )
    new = pd.DataFrame(
        {
            "date": ["2026-01-02", "2026-01-03"],
            "code": ["000001", "000001"],
            "close": [12.0, 13.0],
        }
    )

    merged = merge_raw_data(old, new)

    assert len(merged) == 4
    updated = merged[(merged["code"] == "000001") & (merged["date"] == pd.Timestamp("2026-01-02"))]
    assert updated["close"].iloc[0] == 12.0
    assert merged["date"].max() == pd.Timestamp("2026-01-03")


def test_merge_raw_data_normalizes_string_prices_from_a_fallback_source():
    old = pd.DataFrame({"date": ["2026-01-01"], "code": ["000001"], "open": [10.0], "close": [10.5]})
    new = pd.DataFrame(
        {"日期": ["2026-01-02"], "股票代码": ["000001"], "开盘": ["10.80"], "收盘": ["11.20"]}
    )

    merged = merge_raw_data(old, new)

    added = merged[merged["date"] == pd.Timestamp("2026-01-02")].iloc[0]
    assert merged["open"].dtype.kind == "f"
    assert added["open"] == 10.8
    assert added["close"] == 11.2
