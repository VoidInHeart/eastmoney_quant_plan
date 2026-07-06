from eastmoney_quant.backtest import run_backtest
from eastmoney_quant.data_clean import clean_daily_data
from eastmoney_quant.data_fetch import make_sample_data
from eastmoney_quant.factor import build_factors
from eastmoney_quant.signal import build_monthly_signal


def test_sample_pipeline_runs():
    raw = make_sample_data(codes=25, start_date="20200101", end_date="20211231")
    clean = clean_daily_data(raw)
    factors = build_factors(clean)
    signal = build_monthly_signal(factors, top_n=10)
    result = run_backtest(factors, signal)

    assert not signal.empty
    assert not result["nav"].empty
    assert result["nav"]["nav"].iloc[-1] > 0
    assert "annual_return" in result["performance"]
