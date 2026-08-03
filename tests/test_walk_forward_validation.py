import pandas as pd

from scripts.walk_forward_validation import run_walk_forward


def _factor_like_data() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=310)
    rows = []
    for day, date in enumerate(dates):
        for index in range(40):
            code = f"{index + 1:06d}"
            open_price = 10 + index * 0.1 + day * 0.01
            rows.append(
                {
                    "date": date,
                    "code": code,
                    "name": f"Sample{code}",
                    "open": open_price,
                    "close": open_price * (1 + ((index + day) % 5) * 0.001),
                    "amount_ma20": 30_000_000,
                    "listing_days": 240 + day,
                    "trend_120": 0.03,
                    "drawdown_120": -0.06,
                    "vol_60": -0.01,
                    "score": float(-((index - day) % 40)),
                }
            )
    return pd.DataFrame(rows)


def test_walk_forward_marks_daily_and_executes_only_after_the_signal_close():
    factors = _factor_like_data()
    dates = sorted(factors["date"].unique())
    start_date = dates[-16]
    end_date = dates[-1]

    result = run_walk_forward(factors, start_date=start_date, end_date=end_date, top_n=10)

    nav = result["nav"]
    trades = result["trades"]
    performance = result["performance"]
    assert len(nav) == 15  # First executable open plus 14 daily open-to-open marks.
    assert pd.Timestamp(trades.iloc[0]["signal_date"]) == start_date
    assert pd.Timestamp(trades.iloc[0]["execution_date"]) == dates[-15]
    assert trades.iloc[0]["cost"] > 0
    assert performance["marked_trading_days"] == 15
    assert performance["first_execution_date"] == pd.Timestamp(dates[-15]).date().isoformat()
    assert performance["end_date"] == pd.Timestamp(end_date).date().isoformat()
