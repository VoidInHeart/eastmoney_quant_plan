import pandas as pd

from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.prediction import track_logged_predictions
from eastmoney_quant.signal import build_daily_signal, build_frequency_signal


def _daily_data() -> pd.DataFrame:
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


def test_daily_signal_limits_replacements_and_uses_open_execution():
    data = _daily_data()
    signal = build_daily_signal(
        data,
        top_n=10,
        trading_days=30,
        rank_buffer=5,
        max_replacements=2,
        min_holding_days=2,
    )
    result = run_backtest(data, signal)

    holdings_by_date = [set(frame["code"]) for _, frame in signal.groupby("date")]
    assert signal["date"].nunique() == 31
    assert all(len(current - previous) <= 2 for previous, current in zip(holdings_by_date, holdings_by_date[1:]))
    assert len(result["nav"]) == 30
    assert result["trades"]["stamp_tax_cost"].gt(0).any()
    assert result["trades"]["slippage_cost"].gt(0).any()


def test_prediction_tracking_matches_zero_padded_stock_codes():
    factor_data = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-03", "2026-07-10"]),
            "code": ["000001", "000001"],
            "close": [10.0, 11.0],
        }
    )
    log = pd.DataFrame({"signal_date": pd.to_datetime(["2026-07-03"]), "code": ["000001"], "weight": [1.0]})

    tracking = track_logged_predictions(factor_data, log)

    assert len(tracking) == 1
    assert abs(tracking["portfolio_return"].iloc[0] - 0.1) < 1e-12


def test_weekly_signal_uses_weekly_annualization_and_normalized_weights():
    data = _daily_data()
    signal = build_frequency_signal(
        data,
        frequency="weekly",
        trading_days=30,
        rank_buffer=50,
        max_replacements=2,
        min_holding_days=15,
        min_score_improvement=0.05,
    )
    result = run_backtest(data, signal)

    assert result["performance"]["annualization_periods"] == 52
    assert signal.groupby("date")["weight"].sum().round(12).eq(1).all()
