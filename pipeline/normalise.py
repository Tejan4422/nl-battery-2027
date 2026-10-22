"""Bring price series to one hourly UTC grid and cut out a local calendar year.

Everything is kept in UTC internally. A local (Europe/Amsterdam) calendar year
is then simply a contiguous UTC range, which has 8760 or 8784 hours: the 23-hour
day in March and the 25-hour day in October cancel out.
"""

import pandas as pd

TZ = "Europe/Amsterdam"


def local_year_bounds(year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC start (inclusive) and end (exclusive) of a local calendar year."""
    start = pd.Timestamp(f"{year}-01-01", tz=TZ).tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz=TZ).tz_convert("UTC")
    return start, end


def resolution_minutes(s: pd.Series) -> int:
    """Most common step between timestamps, in minutes."""
    steps = s.index.to_series().diff().dropna()
    if steps.empty:
        raise ValueError("need at least two timestamps to infer resolution")
    return int(steps.mode().iloc[0] / pd.Timedelta(minutes=1))


def to_hourly(s: pd.Series) -> pd.Series:
    """Average sub-hourly prices to hourly. Hourly input is returned unchanged.

    A day-ahead price is a price per MWh for a delivery period, so the hourly
    equivalent of four 15-minute prices is their plain mean (equal durations).
    Hours with only some of their quarters present get the mean of what is
    there; missing whole hours stay missing and are handled by validation.
    """
    if s.index.tz is None:
        raise ValueError("index must be timezone-aware")
    s = s.tz_convert("UTC") if str(s.index.tz) != "UTC" else s
    res = resolution_minutes(s)
    if res == 60:
        return s
    if res > 60 or 60 % res:
        raise ValueError(f"unsupported resolution: {res} minutes")
    hourly = s.resample("1h").mean().dropna()
    hourly.name = s.name
    return hourly


def slice_local_year(s: pd.Series, year: int) -> pd.Series:
    start, end = local_year_bounds(year)
    return s[(s.index >= start) & (s.index < end)]
