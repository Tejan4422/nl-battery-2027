"""Build the price and profile JSON for the website.

    uv run python -m pipeline.export                                  # ENTSO-E CSVs, last 12 complete months
    uv run python -m pipeline.export --window last12 2025             # also calendar year 2025
    uv run python -m pipeline.export --start 2025-04-01 --end 2026-03-31
    uv run python -m pipeline.export --source ember --window 2024
    uv run python -m pipeline.export --source entsoe-api --window 2025

Sources
    entsoe-csv  exports from the ENTSO-E Transparency Platform web UI in
                data/raw/entsoe_nl_dayahead_prices_*.csv (default)
    entsoe-api  the ENTSO-E API (needs ENTSOE_API_KEY, see README)
    ember       Ember's hourly CSV in data/raw/ember_nl_hourly.csv

Outputs, for each window <id> = <first day>_<last day>:
    web/public/data/prices_nl_<id>.json     hourly prices (see below)
    web/public/data/profiles_nl_<id>.json   load and solar profiles on the same hours
    web/public/data/index.json              list of available windows and the default
    data/processed/prices_nl_15min.csv      the combined quarter-hour series (not committed)

Price JSON:
    {
      "zone": "NL", "window": "2025-10-01_2026-09-30", "start_local": "2025-10-01",
      "end_local": "2026-09-30", "source": "ENTSO-E Transparency Platform",
      "unit": "EUR/MWh", "resolution_minutes": 60, "start_utc": "2025-09-30T22:00:00Z",
      "utc_offsets": [[0, 2], [...]],   # [hour index, UTC offset in hours]
      "prices": [...]                   # one value per hour
    }

`utc_offsets` lets the browser map hour index to local clock time without a
timezone library: hour i is at start_utc + i hours, shown at that UTC offset.
"""

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .normalise import TZ, Window, incomplete_hours, last_complete_months, slice_window, to_hourly
from .validate import validate_window

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "public" / "data"
PROCESSED_DIR = ROOT / "data" / "processed"
RAW_DIR = ROOT / "data" / "raw"
DEFAULT_EMBER = RAW_DIR / "ember_nl_hourly.csv"
ENTSOE_GLOB = "entsoe_nl_dayahead_prices_*.csv"

SOURCE_LABEL = {
    "entsoe-csv": "ENTSO-E Transparency Platform",
    "entsoe-api": "ENTSO-E Transparency Platform",
    "ember": "Ember",
}


def utc_offset_changes(index: pd.DatetimeIndex) -> list[list[int]]:
    offsets = np.array([t.utcoffset().total_seconds() / 3600 for t in index.tz_convert(TZ)])
    change = np.flatnonzero(np.r_[True, offsets[1:] != offsets[:-1]])
    return [[int(i), int(offsets[i])] for i in change]


def price_payload(s: pd.Series, w: Window, source: str) -> dict:
    return {
        "zone": "NL",
        "window": w.id,
        "start_local": w.start.isoformat(),
        "end_local": w.end.isoformat(),
        "source": SOURCE_LABEL.get(source, source),
        "unit": "EUR/MWh",
        "resolution_minutes": 60,
        "start_utc": s.index[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc_offsets": utc_offset_changes(s.index),
        "prices": [round(float(v), 2) for v in s.to_numpy()],
    }


def load_source(source: str, inputs: list[Path], windows: list[Window]) -> pd.Series:
    """Raw series at native resolution, UTC index."""
    if source == "entsoe-csv":
        from .ingest_entsoe_csv import read_entsoe_csvs

        paths = inputs or sorted(RAW_DIR.glob(ENTSOE_GLOB))
        if not paths:
            raise SystemExit(f"no files matching data/raw/{ENTSOE_GLOB}")
        print(f"reading {', '.join(p.name for p in paths)}")
        return read_entsoe_csvs(paths)
    if source == "ember":
        from .ingest_ember import read_ember

        return read_ember(inputs[0] if inputs else DEFAULT_EMBER)
    from .ingest_entsoe import fetch_entsoe

    years = sorted({y for w in windows for y in range(w.start.year, w.end.year + 1)})
    return pd.concat([fetch_entsoe(y) for y in years]).sort_index()


def parse_window(spec: str, raw: pd.Series | None) -> Window:
    if spec == "last12":
        if raw is None:
            raise SystemExit("--window last12 needs a file source")
        return last_complete_months(raw)
    if spec.isdigit():
        return Window.year(int(spec))
    return Window.from_id(spec)


def build_prices(raw: pd.Series, w: Window, source: str) -> tuple[pd.Series, dict]:
    window_raw = slice_window(raw, w)
    partial = incomplete_hours(window_raw)
    if len(partial):
        print(f"  WARNING {len(partial)} hours average fewer than four quarter-hours, first {partial[0]}")
    clean, report = validate_window(to_hourly(window_raw), w)
    print(report.summary())
    return clean, price_payload(clean, w, source)


def write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=str))
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def update_index(entries: list[dict], default: str | None) -> None:
    path = OUT_DIR / "index.json"
    index = json.loads(path.read_text()) if path.exists() else {"default": None, "windows": []}
    known = {w["id"]: w for w in index["windows"]}
    for e in entries:
        known[e["id"]] = e
    index["windows"] = sorted(known.values(), key=lambda w: w["start"], reverse=True)
    if default:
        index["default"] = default
    if index["default"] not in known:
        index["default"] = index["windows"][0]["id"]
    write_json(index, path)


def main(argv: list[str] | None = None) -> None:
    from .profiles import profile_payload

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["entsoe-csv", "entsoe-api", "ember"], default="entsoe-csv")
    ap.add_argument("--input", type=Path, nargs="*", default=[], help="input file(s); default depends on source")
    ap.add_argument("--window", nargs="+", default=None,
                    help="last12 (default), a calendar year (2025) or <first>_<last> dates")
    ap.add_argument("--start", type=date.fromisoformat, help="first local date (with --end)")
    ap.add_argument("--end", type=date.fromisoformat, help="last local date, inclusive")
    ap.add_argument("--default", action="store_true", help="make the (first) window the site default")
    args = ap.parse_args(argv)

    specs = args.window or ([] if args.start else ["last12"])
    pre = [Window(args.start, args.end)] if args.start else []
    pre += [parse_window(s, None) for s in specs if s != "last12"]
    raw = load_source(args.source, args.input, pre)
    if args.source == "entsoe-csv":
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        q = raw.tz_convert(TZ).rename("price_eur_mwh")
        q.index.name = "time_local"
        q.to_csv(PROCESSED_DIR / "prices_nl_15min.csv")
        to_hourly(raw).tz_convert(TZ).rename("price_eur_mwh").to_csv(PROCESSED_DIR / "prices_nl_hourly.csv")
        print(f"wrote data/processed/prices_nl_15min.csv and prices_nl_hourly.csv ({len(raw)} quarter-hours)")

    windows = ([Window(args.start, args.end)] if args.start else []) + [parse_window(s, raw) for s in specs]
    entries = []
    for w in windows:
        _, payload = build_prices(raw, w, args.source)
        write_json(payload, OUT_DIR / f"prices_nl_{w.id}.json")
        write_json(profile_payload(w), OUT_DIR / f"profiles_nl_{w.id}.json")
        entries.append({"id": w.id, "start": w.start.isoformat(), "end": w.end.isoformat(),
                        "source": SOURCE_LABEL[args.source]})
    is_default = args.default or (args.window is None and not args.start)
    update_index(entries, windows[0].id if is_default else None)


if __name__ == "__main__":
    main()
