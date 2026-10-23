"""Read day-ahead price exports downloaded from the ENTSO-E Transparency Platform web UI.

Source: https://transparency.entsoe.eu (Market > Energy Prices, BZN|NL, export CSV)

Expected columns: "MTU (CET/CEST)", "Area", "Sequence", "Day-ahead Price (EUR/MWh)",
plus intraday columns that are ignored. Each row is a delivery period in local
time, e.g.

    "01/01/2025 00:00:00 - 01/01/2025 00:15:00"

On DST days the platform adds the zone to disambiguate:

    "30/03/2025 01:45:00 (CET) - 30/03/2025 03:00:00 (CEST)"      spring, 23-hour day
    "26/10/2025 02:00:00 (CEST) - 26/10/2025 02:15:00 (CEST)"     autumn, first 02:00
    "26/10/2025 02:00:00 (CET) - 26/10/2025 02:15:00 (CET)"       autumn, second 02:00

If the zone label is missing on a repeated autumn hour, the first occurrence
is taken as summer time (CEST) and the second as winter time (CET), which is
the order the platform exports them in.

Before 1 October 2025 the NL day-ahead market cleared hourly; exports still
show 15-minute rows with each hour's price repeated four times. Averaging back
to hourly gives the original price, so no special handling is needed.
"""

import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from .normalise import TZ

MTU_COL = "MTU (CET/CEST)"
PRICE_COL = "Day-ahead Price (EUR/MWh)"
MISSING = {"", "n/e", "N/A", "-"}
OFFSET = {"CET": 1, "CEST": 2}

_STAMP = r"(\d\d/\d\d/\d{4} \d\d:\d\d(?::\d\d)?)(?: \((CET|CEST)\))?"
_MTU = re.compile(rf"^{_STAMP} - {_STAMP}$")


class EntsoeFormatError(ValueError):
    pass


def _parse_local(stamp: str) -> pd.Timestamp:
    fmt = "%d/%m/%Y %H:%M:%S" if stamp.count(":") == 2 else "%d/%m/%Y %H:%M"
    return pd.Timestamp(pd.to_datetime(stamp, format=fmt))


def _to_utc(naive: pd.Timestamp, label: str | None, seen: Counter) -> pd.Timestamp:
    if label:
        return (naive - pd.Timedelta(hours=OFFSET[label])).tz_localize("UTC")
    as_summer = naive.tz_localize(TZ, ambiguous=True, nonexistent="NaT")
    if as_summer is pd.NaT:
        raise EntsoeFormatError(f"local time {naive} does not exist (spring DST gap)")
    as_winter = naive.tz_localize(TZ, ambiguous=False)
    if as_summer == as_winter:
        return as_summer.tz_convert("UTC")
    # repeated autumn hour without a label: first time CEST, then CET
    seen[naive] += 1
    return (as_summer if seen[naive] == 1 else as_winter).tz_convert("UTC")


def _duration_minutes(start: pd.Timestamp, end_naive: pd.Timestamp, label: str | None) -> float:
    """Length of a period. An unlabelled end stamp inside the repeated autumn
    hour is ambiguous; take the offset that gives a period of at most an hour."""
    offsets = [OFFSET[label]] if label else [2, 1]
    lengths = [((end_naive - pd.Timedelta(hours=o)).tz_localize("UTC") - start) / pd.Timedelta(minutes=1)
               for o in offsets]
    if not label:
        a = end_naive.tz_localize(TZ, ambiguous=True, nonexistent="NaT")
        if a is not pd.NaT and a == end_naive.tz_localize(TZ, ambiguous=False):
            return (a.tz_convert("UTC") - start) / pd.Timedelta(minutes=1)
    valid = [x for x in lengths if 0 < x <= 60]
    return min(valid) if valid else lengths[0]


def read_entsoe_csv(path: str | Path, area: str = "BZN|NL") -> pd.Series:
    """Return prices in EUR/MWh indexed by UTC start of each delivery period.
    Missing prices (empty, n/e) are dropped; validation treats them as gaps."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {MTU_COL, PRICE_COL, "Area"} - set(df.columns)
    if missing:
        raise EntsoeFormatError(f"{path}: missing columns {sorted(missing)}")
    areas = set(df["Area"])
    if areas != {area}:
        raise EntsoeFormatError(f"{path}: expected only area {area!r}, found {sorted(areas)}")
    if "Sequence" in df.columns and len(set(df["Sequence"])) > 1:
        raise EntsoeFormatError(f"{path}: several sequences {sorted(set(df['Sequence']))}; export one")

    seen: Counter = Counter()
    starts, values = [], []
    for mtu, price in zip(df[MTU_COL], df[PRICE_COL]):
        m = _MTU.match(mtu.strip())
        if not m:
            raise EntsoeFormatError(f"{path}: cannot parse MTU {mtu!r}")
        s_local, s_lab, e_local, e_lab = m.groups()
        start = _to_utc(_parse_local(s_local), s_lab, seen)
        minutes = _duration_minutes(start, _parse_local(e_local), e_lab)
        if minutes not in (15, 30, 60):
            raise EntsoeFormatError(f"{path}: unexpected period length {minutes} min in {mtu!r}")
        p = price.strip()
        starts.append(start)
        values.append(np.nan if p in MISSING else float(p))

    s = pd.Series(values, index=pd.DatetimeIndex(starts, name="time_utc"), name="price_eur_mwh")
    if s.index.has_duplicates:
        dup = s.index[s.index.duplicated()][0]
        raise EntsoeFormatError(f"{path}: duplicate period starting {dup}")
    return s.sort_index().dropna()


def read_entsoe_csvs(paths: list[str | Path], area: str = "BZN|NL") -> pd.Series:
    """Combine several exports (e.g. one per year). Overlapping periods must agree."""
    parts = [read_entsoe_csv(p, area) for p in paths]
    combined = pd.concat(parts).sort_index()
    dup = combined.index.duplicated(keep=False)
    if dup.any():
        overlap = combined[dup].groupby(level=0).nunique()
        conflicts = overlap[overlap > 1]
        if len(conflicts):
            raise EntsoeFormatError(f"files disagree on {len(conflicts)} periods, first {conflicts.index[0]}")
        combined = combined[~combined.index.duplicated(keep="first")]
    return combined
