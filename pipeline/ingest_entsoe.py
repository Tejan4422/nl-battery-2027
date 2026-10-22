"""Optional: fetch day-ahead prices for the Netherlands from the ENTSO-E API.

Requires `uv sync --extra entsoe` and an ENTSOE_API_KEY in the environment or in
a .env file (never commit it). See README for how to request a token.
"""

import os

import pandas as pd

TZ = "Europe/Amsterdam"


def fetch_entsoe(year: int, country_code: str = "NL") -> pd.Series:
    """Return prices in EUR/MWh indexed by UTC timestamp, at native resolution
    (hourly before Oct 2025, 15-minute after)."""
    try:
        from dotenv import load_dotenv
        from entsoe import EntsoePandasClient
    except ImportError as e:
        raise SystemExit("Install the optional dependencies: uv sync --extra entsoe") from e

    load_dotenv()
    key = os.environ.get("ENTSOE_API_KEY")
    if not key:
        raise SystemExit("ENTSOE_API_KEY is not set (put it in .env, see .env.example)")

    client = EntsoePandasClient(api_key=key)
    start = pd.Timestamp(f"{year}-01-01", tz=TZ)
    end = pd.Timestamp(f"{year + 1}-01-01", tz=TZ)
    s = client.query_day_ahead_prices(country_code, start=start, end=end)
    s = s.tz_convert("UTC").astype(float).rename("price_eur_mwh")
    s.index.name = "time_utc"
    return s.sort_index()
