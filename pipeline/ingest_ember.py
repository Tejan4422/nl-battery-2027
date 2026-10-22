"""Read Ember's European wholesale price CSV for one country.

Source: https://ember-energy.org/data/european-wholesale-electricity-price-data
Expected columns: Country, ISO3 Code, Datetime (UTC), Datetime (Local), Price (EUR/MWhe)
"""

from pathlib import Path

import pandas as pd

UTC_COL = "Datetime (UTC)"
PRICE_COL = "Price (EUR/MWhe)"


def read_ember(path: str | Path) -> pd.Series:
    """Return prices in EUR/MWh indexed by UTC timestamp (start of interval)."""
    df = pd.read_csv(path)
    missing = {UTC_COL, PRICE_COL} - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    index = pd.to_datetime(df[UTC_COL], utc=True)
    s = pd.Series(df[PRICE_COL].astype(float).to_numpy(), index=index, name="price_eur_mwh")
    s.index.name = "time_utc"
    return s.sort_index()
