import pandas as pd
import pytest

from eastmoney_quant.adaptive_candidate import (
    AdaptiveCandidateConfig,
    apply_weight_guardrails,
    regime_for_breadth,
)


def test_regime_for_breadth_enforces_the_five_to_ten_name_mandate():
    candidate = AdaptiveCandidateConfig()

    assert regime_for_breadth(0.70, candidate).name == "strong"
    assert regime_for_breadth(0.70, candidate).holdings == 10
    assert regime_for_breadth(0.50, candidate).holdings == 8
    defensive = regime_for_breadth(0.20, candidate)
    assert defensive.name == "defensive"
    assert defensive.holdings == 5
    assert defensive.exposure == 0.50


def test_weight_guardrails_cap_positions_and_leave_entry_cash_unallocated():
    selected = pd.DataFrame({"code": ["000001", "000002"], "weight": [0.5, 0.5]})

    weights = apply_weight_guardrails(
        selected,
        exposure=1.0,
        max_position_weight=0.30,
        new_codes={"000002"},
        entry_fraction=0.50,
    )

    assert weights.tolist() == [0.30, 0.25]
    assert weights.sum() == pytest.approx(0.55)


def test_candidate_rejects_more_than_ten_holdings():
    with pytest.raises(ValueError, match="capped at 10"):
        AdaptiveCandidateConfig(strong_holdings=11)
