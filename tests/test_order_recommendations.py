import pandas as pd
import pytest

from eastmoney_quant.data_fetch import parse_tencent_quote_payload
from eastmoney_quant.orders import (
    build_buy_recommendations,
    build_exposure_reduction_recommendations,
    build_initial_buy_recommendations_from_prescreen,
    build_open_price_template,
)
from eastmoney_quant.prediction import live_holding_regime
from eastmoney_quant.trading_calendar import latest_completed_a_share_trading_day, next_a_share_trading_day
from eastmoney_quant.update import infer_update_window


def _portfolio() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "signal_date": ["2026-07-31", "2026-07-31"],
            "execution_date": ["2026-08-03", "2026-08-03"],
            "rank": [1, 2],
            "code": ["000001", "000002"],
            "name": ["One", "Two"],
            "reference_close": [10.0, 20.0],
        }
    )


def test_orders_use_open_limit_guardrail_and_whole_board_lots():
    orders = build_buy_recommendations(
        _portfolio(),
        pd.DataFrame({"code": ["000001", "000002"], "open_price": [10.0, 21.0]}),
        total_assets=20_000,
    )

    first, second = orders.iloc[0], orders.iloc[1]
    assert first["buy_limit_price"] == 10.03
    assert first["quantity"] == 900
    assert second["buy_limit_price"] == 20.6
    assert second["quantity"] == 400
    assert (orders["quantity"] % 100).eq(0).all()
    assert (orders["estimated_total_cost"] <= orders["target_budget"]).all()


def test_orders_require_an_open_price_for_every_selected_stock():
    with pytest.raises(ValueError, match="000002"):
        build_buy_recommendations(
            _portfolio(),
            pd.DataFrame({"code": ["000001"], "open_price": [10.0]}),
            total_assets=20_000,
        )


def test_orders_reject_a_legacy_portfolio_larger_than_ten_names():
    portfolio = pd.concat([_portfolio()] * 6, ignore_index=True)
    portfolio["code"] = [f"{index + 1:06d}" for index in range(len(portfolio))]
    opens = pd.DataFrame({"code": portfolio["code"], "open_price": 10.0})

    with pytest.raises(ValueError, match="12 stocks"):
        build_buy_recommendations(portfolio, opens)


def test_prescreen_orders_skip_large_opening_gaps_before_selecting_holdings():
    prescreen = pd.DataFrame(
        {
            "signal_date": ["2026-07-31"] * 20,
            "execution_date": ["2026-08-03"] * 20,
            "rank": list(range(1, 21)),
            "code": [f"{index:06d}" for index in range(1, 21)],
            "name": [f"Name{index}" for index in range(1, 21)],
            "previous_close": [10.0] * 20,
            "target_holdings": [6] * 20,
        }
    )
    opens = pd.DataFrame({"code": prescreen["code"], "open_price": [10.5] + [10.0] * 19})

    orders = build_initial_buy_recommendations_from_prescreen(prescreen, opens, total_assets=120_000)

    assert orders["code"].tolist() == [f"{index:06d}" for index in range(2, 8)]
    assert orders["selected_holdings"].eq(6).all()
    assert orders["target_holdings"].eq(6).all()


def test_open_price_template_keeps_the_planned_execution_date():
    template = build_open_price_template(_portfolio())

    assert template.columns.tolist() == [
        "signal_date",
        "execution_date",
        "rank",
        "code",
        "name",
        "previous_close",
        "open_price",
    ]
    assert template["execution_date"].iloc[0] == "2026-08-03"


def test_completed_and_next_trading_days_use_exchange_calendar():
    calendar = ["2026-07-31", "2026-08-03", "2026-08-04"]

    assert latest_completed_a_share_trading_day("2026-08-01 12:00", calendar) == pd.Timestamp("2026-07-31")
    assert latest_completed_a_share_trading_day("2026-08-03 10:00", calendar) == pd.Timestamp("2026-07-31")
    assert latest_completed_a_share_trading_day("2026-08-03 16:00", calendar) == pd.Timestamp("2026-08-03")
    assert next_a_share_trading_day("2026-07-31", calendar) == pd.Timestamp("2026-08-03")


def test_update_window_ends_at_the_last_completed_trading_day():
    raw = pd.DataFrame({"date": ["2026-07-30", "2026-07-31"], "code": ["000001", "000001"], "close": [10, 11]})
    calendar = ["2026-07-31", "2026-08-03"]

    start, end = infer_update_window(raw, 15, as_of="2026-08-01 12:00", calendar=calendar)

    assert start == "20260716"
    assert end == "20260731"


def _breadth_data(positive_fraction: float) -> pd.DataFrame:
    rows = []
    for index in range(100):
        rows.append(
            {
                "date": "2026-07-31",
                "code": f"{index + 1:06d}",
                "name": f"Name{index}",
                "score": float(index),
                "open": 10.0,
                "close": 10.0,
                "listing_days": 300,
                "amount_ma20": 30_000_000,
                "trend_120": 0.01 if index < 100 * positive_fraction else -0.01,
                "drawdown_120": -0.1,
                "vol_60": 0.01,
            }
        )
    return pd.DataFrame(rows)


def test_live_holding_regime_is_capped_at_ten_and_compacts_when_breadth_weakens():
    strong = live_holding_regime(_breadth_data(0.75), pd.Timestamp("2026-07-31"))
    neutral = live_holding_regime(_breadth_data(0.50), pd.Timestamp("2026-07-31"))
    defensive = live_holding_regime(_breadth_data(0.30), pd.Timestamp("2026-07-31"))

    assert strong == ("strong", 0.75, 10)
    assert neutral == ("neutral", 0.5, 6)
    assert defensive == ("defensive", 0.3, 5)


def test_tencent_quote_parser_exposes_today_open_and_previous_close():
    fields = [""] * 31
    fields[1] = "Test"
    fields[2] = "600177"
    fields[3] = "7.81"
    fields[4] = "7.85"
    fields[5] = "7.78"
    fields[30] = "20260803101537"

    quotes = parse_tencent_quote_payload(f'v_sh600177="{"~".join(fields)}";')

    assert quotes.to_dict(orient="records") == [
        {
            "code": "600177",
            "name": "Test",
            "last_price": 7.81,
            "previous_close": 7.85,
            "open_price": 7.78,
            "quote_timestamp": "20260803101537",
        }
    ]


def test_exposure_reduction_keeps_market_value_at_or_below_target():
    holdings = pd.DataFrame(
        {
            "code": ["600177", "601838", "000429", "601988", "001872"],
            "name": ["A", "B", "C", "D", "E"],
            "quantity": [5100, 2100, 2700, 6600, 1800],
        }
    )
    quotes = pd.DataFrame(
        {
            "code": ["600177", "601838", "000429", "601988", "001872"],
            "last_price": [7.68, 18.16, 14.55, 5.84, 21.81],
            "quote_timestamp": ["20260804101310"] * 5,
        }
    )

    orders = build_exposure_reduction_recommendations(holdings, quotes, total_assets=200_000, target_exposure=0.5)

    assert orders["sell_quantity"].tolist() == [2500, 1000, 1300, 3200, 900]
    assert (orders["sell_quantity"] % 100).eq(0).all()
    assert orders["target_remaining_value"].sum() <= 100_000


def test_exposure_reduction_can_be_phased_without_changing_the_final_target():
    holdings = pd.DataFrame(
        {
            "code": ["600177", "601838", "000429", "601988", "001872"],
            "name": ["A", "B", "C", "D", "E"],
            "quantity": [5100, 2100, 2700, 6600, 1800],
        }
    )
    quotes = pd.DataFrame(
        {
            "code": ["600177", "601838", "000429", "601988", "001872"],
            "last_price": [7.68, 18.16, 14.55, 5.84, 21.81],
        }
    )

    orders = build_exposure_reduction_recommendations(
        holdings,
        quotes,
        total_assets=200_000,
        target_exposure=0.5,
        sell_today_fraction=0.4,
    )

    assert orders["sell_quantity"].tolist() == [1000, 400, 500, 1300, 400]
    assert orders["remaining_sell_quantity"].tolist() == [1500, 600, 800, 1900, 500]
    assert orders["planned_total_sell_quantity"].tolist() == [2500, 1000, 1300, 3200, 900]
    assert orders["target_remaining_value"].sum() <= 100_000


def test_exposure_reduction_accepts_actual_holdings_without_a_name_column():
    orders = build_exposure_reduction_recommendations(
        pd.DataFrame({"code": ["000001"], "quantity": [2000]}),
        pd.DataFrame({"code": ["000001"], "last_price": [100.0]}),
        total_assets=200_000,
        target_exposure=0.5,
    )

    assert orders.loc[0, "name"] == ""
    assert orders.loc[0, "sell_quantity"] == 1000
