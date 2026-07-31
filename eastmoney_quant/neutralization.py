"""Cross-sectional factor preprocessing for a future point-in-time panel."""

from __future__ import annotations

import numpy as np
import pandas as pd


def mad_winsorize(values: pd.Series, n_mad: float = 5.0) -> pd.Series:
    """Clip a cross-section around its median using a robust MAD range."""
    median = values.median()
    mad = (values - median).abs().median()
    if pd.isna(mad) or mad == 0:
        return values.copy()
    return values.clip(lower=median - n_mad * mad, upper=median + n_mad * mad)


def _zscore(values: pd.Series) -> pd.Series:
    std = values.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / std


def neutralize_factors(
    data: pd.DataFrame,
    factor_columns: list[str],
    industry_column: str = "industry",
    market_cap_column: str = "market_cap",
    n_mad: float = 5.0,
) -> pd.DataFrame:
    """MAD-clip, standardize, then neutralize factors by industry and log size.

    Inputs must already be aligned to the factor date.  The returned
    ``<factor>_neutral`` columns are residuals re-standardized within each date.
    Rows with incomplete industry or market-cap information retain ``NaN`` so
    callers cannot silently treat unavailable metadata as neutral exposure.
    """
    required = {"date", industry_column, market_cap_column, *factor_columns}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"point-in-time neutralization data missing: {sorted(missing)}")
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    market_cap = pd.to_numeric(result[market_cap_column], errors="coerce")
    result["_log_market_cap"] = np.log(market_cap.where(market_cap > 0))
    for factor in factor_columns:
        result[f"{factor}_neutral"] = np.nan

    for _, index in result.groupby("date", sort=False).groups.items():
        group = result.loc[index]
        controls = pd.get_dummies(group[industry_column].astype("string"), prefix="industry", dtype=float)
        controls.insert(0, "intercept", 1.0)
        controls["log_market_cap"] = group["_log_market_cap"].to_numpy()
        for factor in factor_columns:
            values = pd.to_numeric(group[factor], errors="coerce")
            standardized = _zscore(mad_winsorize(values, n_mad=n_mad))
            valid = (
                standardized.notna()
                & group[industry_column].notna()
                & controls["log_market_cap"].notna()
                & controls.notna().all(axis=1)
            )
            # Require at least one degree of freedom after the regression.
            if valid.sum() <= controls.shape[1]:
                continue
            x = controls.loc[valid].to_numpy(dtype=float)
            y = standardized.loc[valid].to_numpy(dtype=float)
            coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
            residual = pd.Series(y - x @ coefficients, index=standardized.loc[valid].index)
            result.loc[residual.index, f"{factor}_neutral"] = _zscore(residual)
    return result.drop(columns="_log_market_cap")
