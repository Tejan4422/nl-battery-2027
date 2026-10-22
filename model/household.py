"""Household inputs and hour-by-hour energy flows.

Sign conventions (all kWh per hour, all >= 0):
    load L, solar S, battery charge c (AC side), battery discharge d (AC side),
    grid import i, grid export x, curtailed solar u.

Energy balance every hour:
    L + c + x = (S - u) + d + i

Battery state of charge (energy stored, kWh):
    e[t+1] = e[t] + eta * c[t] - d[t] / eta,   eta = sqrt(round-trip efficiency)
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "web" / "public" / "data"


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float
    power_kw: float
    round_trip_efficiency: float = 0.90
    degradation_eur_per_kwh: float = 0.0  # per kWh discharged (AC side)

    @property
    def eta(self) -> float:
        return float(np.sqrt(self.round_trip_efficiency))


@dataclass
class Inputs:
    prices: np.ndarray  # EUR/MWh
    load: np.ndarray  # kWh
    solar: np.ndarray  # kWh
    day_starts: np.ndarray  # hour index of each local midnight, starting with 0
    curtail: bool = False  # inverter may curtail solar when feed-in has negative value

    @property
    def days(self) -> list[tuple[int, int]]:
        ends = np.r_[self.day_starts[1:], len(self.prices)]
        return [(int(a), int(b)) for a, b in zip(self.day_starts, ends)]


def day_starts_from_offsets(n: int, utc_offsets: list[list[int]]) -> np.ndarray:
    """Local-midnight hour indices, given that hour 0 is local midnight and
    `utc_offsets` lists [index, offset_hours] change points."""
    offset = np.zeros(n, dtype=int)
    for idx, off in utc_offsets:
        offset[idx:] = off
    local_hour = (np.arange(n) + offset - offset[0]) % 24
    return np.flatnonzero(local_hour == 0)


def load_inputs(year: int, annual_load_kwh: float, annual_solar_kwh: float,
                curtail: bool = False, data_dir: Path = DATA_DIR) -> Inputs:
    prices = json.loads((data_dir / f"prices_nl_{year}.json").read_text())
    profiles = json.loads((data_dir / f"profiles_nl_{year}.json").read_text())
    if prices["start_utc"] != profiles["start_utc"]:
        raise ValueError("price and profile files do not start at the same hour")
    p = np.array(prices["prices"], dtype=float)
    load = np.array(profiles["load"], dtype=float)
    solar = np.array(profiles["solar"], dtype=float)
    return Inputs(
        prices=p,
        load=load / load.sum() * annual_load_kwh,
        solar=solar / solar.sum() * annual_solar_kwh,
        day_starts=day_starts_from_offsets(len(p), prices["utc_offsets"]),
        curtail=curtail,
    )


def curtailable(inputs: Inputs, sell: np.ndarray) -> np.ndarray:
    """Hours in which exporting solar has a negative value and curtailment is allowed."""
    return inputs.curtail & (np.asarray(sell) < 0)


def simulate(inputs: Inputs, c: np.ndarray, d: np.ndarray, sell: np.ndarray) -> dict:
    """Grid flows for a given battery schedule. Curtails solar export where allowed."""
    net = inputs.load - inputs.solar + c - d
    imp = np.maximum(net, 0.0)
    exp = np.maximum(-net, 0.0)
    u = np.where(curtailable(inputs, sell), np.minimum(exp, inputs.solar), 0.0)
    return {"import": imp, "export": exp - u, "curtailed": u, "charge": c, "discharge": d}


def soc_trajectory(c: np.ndarray, d: np.ndarray, battery: Battery, e0: float = 0.0) -> np.ndarray:
    """State of charge at the start of each hour plus the final value (length n + 1)."""
    return e0 + np.r_[0.0, np.cumsum(battery.eta * c - d / battery.eta)]
