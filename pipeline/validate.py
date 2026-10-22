"""Checks on an hourly price series for one local year.

- duplicates: hard error
- gaps: up to `max_fill_hours` consecutive missing hours are linearly
  interpolated and reported; anything longer is a hard error
- range: negative prices are valid (they happen on sunny, windy days). Values
  outside the plausible band are a hard error, since they point at a unit or
  parsing problem rather than a market outcome.
"""

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .normalise import TZ, local_year_bounds

# SDAC harmonised clearing limits are -500 and +4000 EUR/MWh (the upper limit
# can be raised automatically). The band below leaves headroom for that.
PLAUSIBLE_MIN = -600.0
PLAUSIBLE_MAX = 10_000.0


class ValidationError(ValueError):
    pass


@dataclass
class Report:
    year: int
    hours: int
    filled_gaps: list[tuple[str, int]] = field(default_factory=list)
    short_days: list[str] = field(default_factory=list)
    long_days: list[str] = field(default_factory=list)
    mean: float = 0.0
    min: float = 0.0
    max: float = 0.0
    p05: float = 0.0
    p95: float = 0.0
    negative_hours: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"year {self.year}: {self.hours} hours",
            f"  price EUR/MWh: mean {self.mean:.2f}, min {self.min:.2f}, max {self.max:.2f}, "
            f"p05 {self.p05:.2f}, p95 {self.p95:.2f}",
            f"  negative-price hours: {self.negative_hours} "
            f"({100 * self.negative_hours / self.hours:.1f}%)",
            f"  DST days: 23h {self.short_days}, 25h {self.long_days}",
        ]
        if self.filled_gaps:
            lines.append(f"  WARNING interpolated gaps (start UTC, hours): {self.filled_gaps}")
        return "\n".join(lines)


def _gap_runs(missing: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, int]]:
    """Group missing hourly timestamps into (start, length) runs."""
    runs: list[tuple[pd.Timestamp, int]] = []
    for t in missing:
        if runs and t - runs[-1][0] == pd.Timedelta(hours=runs[-1][1]):
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((t, 1))
    return runs


def validate_year(s: pd.Series, year: int, max_fill_hours: int = 3) -> tuple[pd.Series, Report]:
    """Validate and gap-fill one local year of hourly prices (UTC index)."""
    if s.index.has_duplicates:
        dups = s.index[s.index.duplicated()].unique()
        raise ValidationError(f"{len(dups)} duplicate timestamps, first {dups[0]}")

    start, end = local_year_bounds(year)
    expected = pd.date_range(start, end, freq="1h", inclusive="left")
    extra = s.index.difference(expected)
    if len(extra):
        raise ValidationError(f"{len(extra)} timestamps off the hourly grid, first {extra[0]}")

    s = s.reindex(expected)
    runs = _gap_runs(s.index[s.isna()])
    too_long = [(t, n) for t, n in runs if n > max_fill_hours]
    if too_long:
        raise ValidationError(f"gaps longer than {max_fill_hours}h: {too_long[:5]}")
    if runs and (s.iloc[0] != s.iloc[0] or s.iloc[-1] != s.iloc[-1]):
        raise ValidationError("cannot interpolate a gap at the start or end of the year")
    s = s.interpolate(method="time")

    values = s.to_numpy()
    bad = (values < PLAUSIBLE_MIN) | (values > PLAUSIBLE_MAX)
    if bad.any():
        i = int(np.argmax(bad))
        raise ValidationError(f"implausible price {values[i]} at {s.index[i]}")

    hours_per_day = s.index.tz_convert(TZ).normalize().value_counts()
    report = Report(
        year=year,
        hours=len(s),
        filled_gaps=[(t.isoformat(), n) for t, n in runs],
        short_days=sorted(d.date().isoformat() for d in hours_per_day[hours_per_day == 23].index),
        long_days=sorted(d.date().isoformat() for d in hours_per_day[hours_per_day == 25].index),
        mean=float(values.mean()),
        min=float(values.min()),
        max=float(values.max()),
        p05=float(np.percentile(values, 5)),
        p95=float(np.percentile(values, 95)),
        negative_hours=int((values < 0).sum()),
    )
    odd = hours_per_day[~hours_per_day.isin([23, 24, 25])]
    if len(odd):
        raise ValidationError(f"local days with unexpected hour counts: {odd.to_dict()}")
    return s, report
