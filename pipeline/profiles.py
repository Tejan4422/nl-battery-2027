"""Hourly consumption and solar profiles, normalised to sum to 1 over the year.

The model multiplies these by the annual totals entered by the user, so only
the shape matters here.

Consumption (synthetic, default)
    daily shape (weekday / weekend, local clock time) x monthly factor, plus a
    lighting bump on dark evenings. The numbers are chosen to resemble the
    NEDU E1A residential profile: higher in winter, low at night, an evening
    peak around 18:00-21:00. They are not the official profile. If you have the
    official file, pass it with --load-profile (CSV: local timestamp, value;
    hourly or 15-minute).

Solar (synthetic)
    sun elevation at De Bilt (52.10 N, 5.18 E) at the middle of each hour,
    scaled per month so that monthly yields follow a typical Dutch
    distribution for south-facing panels. This is a clear-sky shape: every
    day in a month has the same profile, so cloudy days are not represented.
    KNMI irradiance data would be the next step.

    uv run python -m pipeline.profiles --year 2025
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .export import OUT_DIR, write_json
from .normalise import TZ, local_year_bounds

LAT, LON = 52.10, 5.18

# Relative consumption by local hour (0..23).
WEEKDAY = np.array([0.55, 0.48, 0.45, 0.44, 0.45, 0.50, 0.70, 0.95, 0.95, 0.80, 0.75, 0.78,
                    0.82, 0.78, 0.74, 0.78, 0.92, 1.20, 1.45, 1.45, 1.35, 1.20, 0.95, 0.72])
WEEKEND = np.array([0.62, 0.52, 0.47, 0.45, 0.45, 0.47, 0.55, 0.70, 0.90, 1.05, 1.10, 1.10,
                    1.08, 1.00, 0.95, 0.95, 1.02, 1.22, 1.42, 1.42, 1.32, 1.20, 1.00, 0.78])
# Relative monthly consumption level (Jan..Dec).
MONTH_LOAD = np.array([1.22, 1.10, 1.03, 0.92, 0.85, 0.80, 0.80, 0.83, 0.90, 1.00, 1.12, 1.25])
# Extra lighting load per hour when the sun is down between 16:00 and 23:00.
LIGHTING = 0.25

# Typical share of annual PV yield per month in the Netherlands (Jan..Dec),
# rounded from PVGIS-style estimates for a south-facing 35 degree roof.
MONTH_SOLAR = np.array([2.6, 4.4, 8.0, 11.6, 13.3, 13.2, 13.4, 11.6, 9.0, 6.4, 3.0, 2.0])


def hour_index(year: int) -> pd.DatetimeIndex:
    start, end = local_year_bounds(year)
    return pd.date_range(start, end, freq="1h", inclusive="left")


def sun_elevation(times_utc: pd.DatetimeIndex, lat: float = LAT, lon: float = LON) -> np.ndarray:
    """Solar elevation angle in degrees (NOAA low-precision formulas, ~0.5 deg)."""
    doy = times_utc.day_of_year.to_numpy()
    hour = times_utc.hour.to_numpy() + times_utc.minute.to_numpy() / 60
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    solar_time = hour * 60 + eqtime + 4 * lon
    ha = np.radians(solar_time / 4 - 180)
    phi = np.radians(lat)
    sin_el = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(ha)
    return np.degrees(np.arcsin(np.clip(sin_el, -1, 1)))


def synthetic_solar(index: pd.DatetimeIndex) -> np.ndarray:
    mid = index + pd.Timedelta(minutes=30)
    el = sun_elevation(mid)
    raw = np.clip(np.sin(np.radians(el)), 0, None) ** 1.2  # crude air-mass damping
    months = index.tz_convert(TZ).month.to_numpy() - 1
    out = np.zeros(len(index))
    shares = MONTH_SOLAR / MONTH_SOLAR.sum()
    for m in range(12):
        mask = months == m
        out[mask] = raw[mask] / raw[mask].sum() * shares[m]
    return out / out.sum()


def synthetic_load(index: pd.DatetimeIndex) -> np.ndarray:
    local = index.tz_convert(TZ)
    hours = local.hour.to_numpy()
    weekend = local.day_of_week.to_numpy() >= 5
    shape = np.where(weekend, WEEKEND[hours], WEEKDAY[hours])
    dark = sun_elevation(index + pd.Timedelta(minutes=30)) < 0
    evening = (hours >= 16) & (hours <= 22)
    shape = shape + LIGHTING * (dark & evening)
    shape = shape * MONTH_LOAD[local.month.to_numpy() - 1]
    return shape / shape.sum()


def load_from_csv(path: Path, index: pd.DatetimeIndex) -> np.ndarray:
    """Read an external profile (e.g. NEDU E1A). Columns: local timestamp, value."""
    df = pd.read_csv(path)
    t = pd.to_datetime(df.iloc[:, 0])
    if t.dt.tz is None:
        t = t.dt.tz_localize(TZ, ambiguous="infer")
    s = pd.Series(df.iloc[:, 1].astype(float).to_numpy(), index=t.dt.tz_convert("UTC"))
    hourly = s.resample("1h").sum()  # energy fractions add up within the hour
    hourly = hourly.reindex(index)
    if hourly.isna().any():
        raise ValueError(f"{path}: profile does not cover the full year")
    return (hourly / hourly.sum()).to_numpy()


def profile_payload(year: int, load_csv: Path | None = None) -> dict:
    index = hour_index(year)
    load = load_from_csv(load_csv, index) if load_csv else synthetic_load(index)
    solar = synthetic_solar(index)
    return {
        "year": year,
        "start_utc": index[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "load_source": str(load_csv.name) if load_csv else "synthetic",
        "solar_source": "synthetic clear-sky, De Bilt",
        # fractions of the annual total; rounded, the model rescales to sum 1
        "load": [float(f"{v:.4e}") for v in load],
        "solar": [float(f"{v:.4e}") for v in solar],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, nargs="+", default=[2025])
    ap.add_argument("--load-profile", type=Path, default=None)
    args = ap.parse_args(argv)
    for year in args.year:
        write_json(profile_payload(year, args.load_profile), OUT_DIR / f"profiles_nl_{year}.json")


if __name__ == "__main__":
    main()
