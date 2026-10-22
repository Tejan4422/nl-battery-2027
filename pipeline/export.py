"""Build the price JSON for the website.

    uv run python -m pipeline.export --year 2025
    uv run python -m pipeline.export --year 2025 --source entsoe

Output: web/public/data/prices_nl_<year>.json

    {
      "zone": "NL", "year": 2025, "unit": "EUR/MWh", "resolution_minutes": 60,
      "start_utc": "2024-12-31T23:00:00Z",
      "utc_offsets": [[0, 1], [2065, 2], [7105, 1]],   # [hour index, UTC offset in hours]
      "prices": [...]                                    # one value per hour
    }

`utc_offsets` lets the browser map hour index to local clock time without a
timezone library: hour i is at start_utc + i hours, shown at that UTC offset.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .normalise import TZ, slice_local_year, to_hourly
from .validate import validate_year

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "public" / "data"
DEFAULT_EMBER = ROOT / "data" / "raw" / "ember_nl_hourly.csv"


def utc_offset_changes(index: pd.DatetimeIndex) -> list[list[int]]:
    offsets = np.array([t.utcoffset().total_seconds() / 3600 for t in index.tz_convert(TZ)])
    change = np.flatnonzero(np.r_[True, offsets[1:] != offsets[:-1]])
    return [[int(i), int(offsets[i])] for i in change]


def price_payload(s: pd.Series, year: int, source: str) -> dict:
    return {
        "zone": "NL",
        "year": year,
        "source": source,
        "unit": "EUR/MWh",
        "resolution_minutes": 60,
        "start_utc": s.index[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc_offsets": utc_offset_changes(s.index),
        "prices": [round(float(v), 2) for v in s.to_numpy()],
    }


def build_prices(year: int, source: str, ember_path: Path) -> tuple[pd.Series, dict]:
    if source == "ember":
        from .ingest_ember import read_ember

        raw = read_ember(ember_path)
    else:
        from .ingest_entsoe import fetch_entsoe

        raw = fetch_entsoe(year)
    hourly = slice_local_year(to_hourly(raw), year)
    clean, report = validate_year(hourly, year)
    print(report.summary())
    return clean, price_payload(clean, year, source)


def write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, nargs="+", default=[2025])
    ap.add_argument("--source", choices=["ember", "entsoe"], default="ember")
    ap.add_argument("--input", type=Path, default=DEFAULT_EMBER, help="Ember CSV path")
    args = ap.parse_args(argv)
    for year in args.year:
        _, payload = build_prices(year, args.source, args.input)
        write_json(payload, OUT_DIR / f"prices_nl_{year}.json")


if __name__ == "__main__":
    main()
