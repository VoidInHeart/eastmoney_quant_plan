import pandas as pd
import pytest

from eastmoney_quant.execution import run_executable_backtest
from eastmoney_quant.neutralization import neutralize_factors
from eastmoney_quant.universe import build_observed_universe, universe_coverage_summary


def test_execution_retries_untradable_order_and_charges_cost_only_on_fill():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"])
    data = pd.DataFrame(
        [
            {"date": date, "code": "000001", "open": 10.0, "close": 10.0}
            for date in dates
        ]
    )
    data.loc[data["date"] == pd.Timestamp("2025-01-03"), "open"] = None
    signal = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "code": ["000001", "000001"],
            "weight": [1.0, 1.0],
        }
    )

    result = run_executable_backtest(data, signal, initial_cash=1_000, max_order_attempts=3)
    events = result["order_events"]

    deferred = events[events["status"] == "deferred_untradable"]
    fills = events[events["status"] == "filled"]
    assert len(deferred) == 1
    assert deferred["cost"].iloc[0] == 0
    assert not fills.empty
    assert fills["cost"].sum() > 0
    assert result["performance"]["deferred_order_events"] == 1


def test_observed_universe_uses_delisting_and_visible_tradability_rules():
    data = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-01", "2020-01-02"]),
            "code": ["000001", "000001", "000002", "000002"],
            "name": ["正常", "正常", "*ST测试", "*ST测试"],
            "open": [10.0, 10.0, 10.0, 10.0],
            "close": [10.0, 10.0, 10.0, 10.0],
        }
    )
    registry = pd.DataFrame(
        {
            "code": ["000001"],
            "listing_date": ["2010-01-01"],
            "delisting_date": ["2020-01-01"],
        }
    )

    universe = build_observed_universe(data, min_observed_days=1, delisting_registry=registry)

    assert universe.loc[universe["code"] == "000001", "is_observed_tradable"].tolist() == [True, False]
    assert not universe.loc[universe["code"] == "000002", "is_observed_tradable"].any()
    summary = universe_coverage_summary(universe)
    assert summary["tradable_rows"] == 1
    assert summary["codes_with_registry_listing_date"] == 1


def test_neutralization_requires_point_in_time_controls_and_returns_residuals():
    data = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02"] * 8),
            "industry": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "market_cap": [10, 20, 30, 40, 15, 25, 35, 45],
            "factor": [1.2, 1.5, 1.7, 2.4, -0.8, -0.3, 0.2, 0.9],
        }
    )

    result = neutralize_factors(data, ["factor"])

    assert result["factor_neutral"].notna().all()
    assert abs(result["factor_neutral"].mean()) < 1e-12
    with pytest.raises(ValueError, match="market_cap"):
        neutralize_factors(data.drop(columns="market_cap"), ["factor"])
