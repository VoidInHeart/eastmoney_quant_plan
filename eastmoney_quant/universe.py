"""Observed point-in-time universe construction.

This module intentionally distinguishes the observed data universe from a full
historical exchange universe.  It can prevent a symbol from being selected
outside the dates visible in the local data and after a known delisting, but it
cannot by itself eliminate survivorship bias until historical constituent/listing
data have been backfilled for the whole market.
"""

from __future__ import annotations

import pandas as pd

from . import config


def load_delisting_registry() -> pd.DataFrame:
    """Load the cached delisting registry, returning a stable empty schema."""
    columns = ["code", "listing_date", "delisting_date"]
    if not config.DELISTING_PATH.exists():
        return pd.DataFrame(columns=columns)
    registry = pd.read_parquet(config.DELISTING_PATH)
    registry = registry.reindex(columns=columns).copy()
    registry["code"] = registry["code"].astype(str).str.zfill(6)
    for column in ("listing_date", "delisting_date"):
        registry[column] = pd.to_datetime(registry[column], errors="coerce")
    return registry.drop_duplicates("code", keep="last")


def build_observed_universe(
    daily_data: pd.DataFrame,
    min_observed_days: int = config.MIN_LISTING_DAYS,
    delisting_registry: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add conservative, date-aware tradability flags to locally observed data.

    ``first_observed_date`` is not asserted to be a true listing date.  It is a
    conservative fallback where complete listing history is not yet available.
    A separate ``has_registry_listing_date`` field makes that limitation visible
    to downstream research scripts.
    """
    required = {"date", "code", "open", "close"}
    missing = required - set(daily_data.columns)
    if missing:
        raise ValueError(f"daily_data missing required columns: {sorted(missing)}")

    result = daily_data.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["code"] = result["code"].astype(str).str.zfill(6)
    result = result.dropna(subset=["date"]).sort_values(["code", "date"]).copy()
    result["first_observed_date"] = result.groupby("code")["date"].transform("min")
    result["observed_days"] = result.groupby("code").cumcount() + 1

    registry = load_delisting_registry() if delisting_registry is None else delisting_registry.copy()
    registry = registry.reindex(columns=["code", "listing_date", "delisting_date"])
    registry["code"] = registry["code"].astype(str).str.zfill(6)
    for column in ("listing_date", "delisting_date"):
        registry[column] = pd.to_datetime(registry[column], errors="coerce")
    result = result.merge(registry.drop_duplicates("code", keep="last"), on="code", how="left")

    names = result.get("name", pd.Series("", index=result.index)).fillna("").astype(str)
    result["has_registry_listing_date"] = result["listing_date"].notna()
    listed_on_date = result["listing_date"].isna() | (result["date"] >= result["listing_date"])
    not_delisted = result["delisting_date"].isna() | (result["date"] <= result["delisting_date"])
    valid_prices = (pd.to_numeric(result["open"], errors="coerce") > 0) & (
        pd.to_numeric(result["close"], errors="coerce") > 0
    )
    is_st = names.str.upper().str.contains("ST", regex=False) | names.str.contains("退", regex=False)
    result["is_observed_tradable"] = (
        listed_on_date
        & not_delisted
        & valid_prices
        & ~is_st
        & (result["observed_days"] >= min_observed_days)
    )
    result["universe_source"] = "observed_history_with_delisting_registry"
    return result.sort_values(["date", "code"]).reset_index(drop=True)


def universe_coverage_summary(universe: pd.DataFrame) -> dict[str, int]:
    """Return compact coverage statistics for reports and experiment metadata."""
    if "is_observed_tradable" not in universe:
        raise ValueError("universe must be created by build_observed_universe")
    return {
        "rows": int(len(universe)),
        "codes": int(universe["code"].nunique()),
        "tradable_rows": int(universe["is_observed_tradable"].sum()),
        "codes_with_registry_listing_date": int(universe.loc[universe["has_registry_listing_date"], "code"].nunique()),
    }
