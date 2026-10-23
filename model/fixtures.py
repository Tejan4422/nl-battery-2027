"""Generate shared test fixtures (JSON) used by both pytest and vitest.

    uv run python -m model.fixtures            # small synthetic cases
    uv run python -m model.fixtures --full     # also 12-month cases on real prices (slow)

Each fixture holds the inputs, battery, tariff parameters and the expected
results of the Python reference (baselines and MILP). The TypeScript suite must
reproduce the baselines exactly and the optimised cost within 1%.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .household import Battery, Inputs, default_window, load_inputs
from .scenario import STRATEGIES, run
from .tariffs import TariffParams, load_yaml, params_from_config

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "fixtures"

SMALL_TARIFF = dict(vat=0.21, energy_tax=0.09, tax_reduction=0.0, grid_fee=0.0, standing_charge=0.0)


def summary(r) -> dict:
    b = r.bill
    return {
        "total": r.total,
        "bill_total": b["total"],
        "energy_cost": b["supply"] + b["feed_in"] + b["energy_tax"],
        "import_kwh": b["import_kwh"],
        "export_kwh": b["export_kwh"],
        "curtailed_kwh": float(r.flows["curtailed"].sum()),
        "discharge_kwh": float(r.flows["discharge"].sum()),
        "degradation": r.degradation,
        "regime": r.regime,
        "consistent": r.consistent,
    }


def expected(tariff, inputs, battery, lookahead_days=1) -> dict:
    return {s: summary(run(s, tariff, inputs, battery, lookahead_days)) for s in STRATEGIES}


def synthetic_inputs(seed: int, day_lengths=(24, 23, 25, 24), curtail=False) -> Inputs:
    """Days of different length to exercise DST handling."""
    rng = np.random.default_rng(seed)
    n = sum(day_lengths)
    h = np.concatenate([np.arange(L) for L in day_lengths])
    prices = 90 + 70 * np.sin((h - 7) / 24 * 2 * np.pi) + rng.normal(0, 35, n)
    prices[rng.random(n) < 0.12] = rng.uniform(-400, -1)
    load = 0.15 + rng.gamma(2, 0.2, n)
    solar = np.clip(np.sin((h - 6) / 14 * np.pi), 0, None) * rng.uniform(0.3, 3.0, n)
    return Inputs(np.round(prices, 2), np.round(load, 4), np.round(solar, 4),
                  np.r_[0, np.cumsum(day_lengths)[:-1]], curtail)


def small_cases():
    batteries = [
        Battery(5.0, 2.5, 0.90, 0.02),
        Battery(10.0, 5.0, 0.85, 0.0),
        Battery(2.7, 1.3, 0.92, 0.05),  # capacity not on the 0.1 kWh grid
    ]
    contracts = {
        "fixed": dict(contract="fixed", supply_price=0.13, feed_in_compensation=0.12, feed_in_cost=0.10),
        "dynamic": dict(contract="dynamic", markup=0.02, feed_in_fee=0.015, feed_in_vat_while_netting=True),
        "dynamic_novat": dict(contract="dynamic", markup=0.02, feed_in_fee=0.015, feed_in_vat_while_netting=False),
    }
    case = 0
    for name, c in contracts.items():
        for netting in (False, True):
            for curtail in (False, True):
                battery = batteries[case % len(batteries)]
                tariff = TariffParams(netting=netting, **SMALL_TARIFF, **c)
                inputs = synthetic_inputs(seed=case, curtail=curtail)
                scale = 1.0
                if case % 4 == 3:  # some net exporters
                    inputs.solar = inputs.solar * 2.5
                    scale = 2.5
                yield {
                    "name": f"small_{case:02d}_{name}_{'netting' if netting else 'nonetting'}"
                            f"{'_curtail' if curtail else ''}",
                    "description": f"4 synthetic days (24/23/25/24 h), solar x{scale}",
                    "inputs": {
                        "prices": inputs.prices.tolist(),
                        "load": inputs.load.tolist(),
                        "solar": inputs.solar.tolist(),
                        "day_starts": inputs.day_starts.tolist(),
                        "curtail": curtail,
                    },
                    "battery": asdict(battery),
                    "tariff": tariff.to_dict(),
                    "lookahead_days": 1,
                    "expected": expected(tariff, inputs, battery),
                }
                case += 1


def full_cases(window=None, annual_load=3500.0, annual_solar=3000.0):
    window = window or default_window()
    cfg = load_yaml()
    batteries = {"5kwh": Battery(5.0, 2.5, 0.90, 0.02), "10kwh": Battery(10.0, 5.0, 0.90, 0.02)}
    inputs = load_inputs(window, annual_load, annual_solar, curtail=True)
    for bname, battery in batteries.items():
        for contract in ("fixed", "dynamic"):
            for rules in ("2026", "2027"):
                tariff = params_from_config(cfg, contract, rules)
                print(f"  {window} {bname} {contract} {rules}")
                yield {
                    "name": f"{window}_{bname}_{contract}_{rules}",
                    "description": f"Prices {window}, {annual_load:.0f} kWh load, {annual_solar:.0f} kWh solar",
                    "inputs": {"window": window, "annual_load_kwh": annual_load, "annual_solar_kwh": annual_solar,
                               "curtail": True},
                    "battery": asdict(battery),
                    "tariff": tariff.to_dict(),
                    "lookahead_days": 1,
                    "expected": expected(tariff, inputs, battery),
                }


def write(fixtures, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(fixtures), indent=1))
    print(f"wrote {path.relative_to(ROOT)}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also generate 12-month fixtures (about a minute)")
    args = ap.parse_args(argv)
    write(small_cases(), FIXTURE_DIR / "small_cases.json")
    if args.full:
        write(full_cases(), FIXTURE_DIR / "year_cases.json")


if __name__ == "__main__":
    main()
