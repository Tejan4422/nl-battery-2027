"""Bring price series to one hourly UTC grid and cut out a local date window.

Everything is kept in UTC internally. A window of local (Europe/Amsterdam)
dates is then simply a contiguous UTC range: a full year has 8760 or 8784
hours because the 23-hour day in March and the 25-hour day in October cancel
out.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

TZ = "Europe/Amsterdam"


@dataclass(frozen=True)
class Window:
    """Local calendar dates, both inclusive."""

    start: date
    end: date

    @property
    def id(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"

    @classmethod
    def from_id(cls, window_id: str) -> "Window":
        a, b = window_id.split("_")
        return cls(date.fromisoformat(a), date.fromisoformat(b))

    @classmethod
    def year(cls, year: int) -> "Window":
        return cls(date(year, 1, 1), date(year, 12, 31))

    def bounds_utc(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """UTC start (inclusive) and end (exclusive)."""
        start = pd.Timestamp(self.start, tz=TZ).tz_convert("UTC")
        end = (pd.Timestamp(self.end, tz=TZ) + pd.DateOffset(days=1)).tz_convert("UTC")
        return start, end


def last_complete_months(s: pd.Series, months: int = 12) -> Window:
    """The most recent `months` complete calendar months covered by `s`."""
    res = pd.Timedelta(minutes=resolution_minutes(s))
    data_end = (s.index.max() + res).tz_convert(TZ)  # exclusive
    # first day of the last, possibly partial, month; if the data ends exactly
    # at a month boundary this is that boundary and the last month is complete
    end_month = data_end.normalize().replace(day=1)
    start_month = end_month - pd.DateOffset(months=months)
    w = Window(start_month.date(), (end_month - pd.DateOffset(days=1)).date())
    first = s.index.min().tz_convert(TZ)
    if first > pd.Timestamp(w.start, tz=TZ):
        raise ValueError(f"data starts {first}, not enough for {months} complete months")
    return w


def local_year_bounds(year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    return Window.year(year).bounds_utc()


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
    there (see `incomplete_hours`); missing whole hours stay missing and are
    handled by validation.
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


def incomplete_hours(s: pd.Series) -> pd.DatetimeIndex:
    """Hours of a sub-hourly series that have fewer values than expected."""
    res = resolution_minutes(s)
    if res >= 60:
        return pd.DatetimeIndex([])
    counts = s.dropna().resample("1h").count()
    counts = counts[counts > 0]
    return counts.index[counts < 60 // res]


def slice_window(s: pd.Series, w: Window) -> pd.Series:
    start, end = w.bounds_utc()
    return s[(s.index >= start) & (s.index < end)]


def slice_local_year(s: pd.Series, year: int) -> pd.Series:
    return slice_window(s, Window.year(year))
