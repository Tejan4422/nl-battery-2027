"""Tariff model: hourly marginal prices and the annual bill.

Two contracts:
- fixed:   one supply price per kWh; feed-in compensation and feed-in cost per kWh
- dynamic: hourly day-ahead price + markup for consumption, day-ahead price minus
           a fee for feed-in

Two sets of rules:
- netting (2026): consumption and feed-in are netted over the year for energy
  tax and (fixed contract) the supply price
- no netting (2027): consumption and feed-in are settled separately

All amounts in EUR, prices per kWh. Parameters are stored excluding VAT; VAT is
added on everything the household buys (supply, energy tax, feed-in costs,
standing charges, grid fee) and not on feed-in revenue, except the dynamic
feed-in under netting when `feed_in_vat_while_netting` is set.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_YAML = ROOT / "config" / "tariffs_nl.yaml"

CONSUMER = "consumer"  # annual import >= export (netting regime)
EXPORTER = "exporter"  # annual export > import


@dataclass(frozen=True)
class TariffParams:
    contract: str  # "fixed" | "dynamic"
    netting: bool
    vat: float
    energy_tax: float
    tax_reduction: float  # EUR/year
    grid_fee: float  # EUR/year
    standing_charge: float  # EUR/year
    supply_price: float = 0.0  # fixed
    feed_in_compensation: float = 0.0  # fixed
    feed_in_cost: float = 0.0  # fixed
    markup: float = 0.0  # dynamic
    feed_in_fee: float = 0.0  # dynamic
    feed_in_vat_while_netting: bool = False  # dynamic

    def to_dict(self) -> dict:
        return asdict(self)


def load_yaml(path: Path = DEFAULT_YAML) -> dict:
    return yaml.safe_load(Path(path).read_text())


def params_from_config(cfg: dict, contract: str, rules: str) -> TariffParams:
    """Build parameters for one contract ('fixed'/'dynamic') and rules year ('2026'/'2027')."""
    r = cfg["rules"][rules]
    ty = r["tax_year"]
    common = dict(
        contract=contract,
        netting=bool(r["netting"]),
        vat=cfg["vat"]["value"],
        energy_tax=cfg["energy_tax"][ty]["value"],
        tax_reduction=cfg["tax_reduction"][ty]["value"],
        grid_fee=cfg["grid_fee"]["value"],
    )
    if contract == "fixed":
        c = cfg["fixed_contract"]
        return TariffParams(
            **common,
            standing_charge=c["standing_charge"]["value"],
            supply_price=c["supply_price"]["value"],
            feed_in_compensation=c["feed_in_compensation"]["value"],
            feed_in_cost=c["feed_in_cost"]["value"],
        )
    if contract == "dynamic":
        c = cfg["dynamic_contract"]
        return TariffParams(
            **common,
            standing_charge=c["standing_charge"]["value"],
            markup=c["markup"]["value"],
            feed_in_fee=c["feed_in_fee"]["value"],
            feed_in_vat_while_netting=bool(c["feed_in_vat_while_netting"]["value"]),
        )
    raise ValueError(f"unknown contract {contract!r}")


def _dynamic_feed_in_factor(t: TariffParams) -> float:
    return 1 + t.vat if (t.netting and t.feed_in_vat_while_netting) else 1.0


def marginal_prices(t: TariffParams, price_eur_mwh: np.ndarray, regime: str = CONSUMER):
    """Hourly cost of one extra kWh imported (buy) and value of one extra kWh exported (sell).

    Without netting these are simply the tariffs. With netting the annual bill
    is piecewise linear in total import I and export X, and the marginal price
    depends on which side of I = X the household ends up:
    - consumer (I >= X): an exported kWh offsets an imported one, so it earns
      the full netted price (supply/energy tax incl. VAT)
    - exporter (I < X): an extra kWh changes the paid-out surplus
    """
    p = np.asarray(price_eur_mwh, dtype=float) / 1000.0
    v = 1 + t.vat
    n = len(p)
    if t.contract == "fixed":
        retail = (t.supply_price + t.energy_tax) * v
        feed_cost = t.feed_in_cost * v
        if t.netting and regime == CONSUMER:
            buy, sell = retail, retail - feed_cost
        elif t.netting:
            buy, sell = t.feed_in_compensation, t.feed_in_compensation - feed_cost
        else:
            buy, sell = retail, t.feed_in_compensation - feed_cost
        return np.full(n, buy), np.full(n, sell)

    k = _dynamic_feed_in_factor(t)
    tax = t.energy_tax * v
    buy = (p + t.markup) * v
    sell = (p - t.feed_in_fee) * k
    if t.netting and regime == CONSUMER:
        buy, sell = buy + tax, sell + tax
    elif not t.netting:
        buy = buy + tax
    return buy, sell


def annual_bill(t: TariffParams, price_eur_mwh: np.ndarray, imp: np.ndarray, exp: np.ndarray) -> dict:
    """Exact annual bill (EUR incl. VAT) from hourly import and export in kWh.

    Returns a breakdown; `total` is what the household pays for the year.
    """
    p = np.asarray(price_eur_mwh, dtype=float) / 1000.0
    imp = np.asarray(imp, dtype=float)
    exp = np.asarray(exp, dtype=float)
    v = 1 + t.vat
    I, X = float(imp.sum()), float(exp.sum())
    taxed = max(I - X, 0.0) if t.netting else I

    if t.contract == "fixed":
        if t.netting:
            supply = t.supply_price * v * max(I - X, 0.0)
            feed_in = -t.feed_in_compensation * max(X - I, 0.0)
        else:
            supply = t.supply_price * v * I
            feed_in = -t.feed_in_compensation * X
        feed_in += t.feed_in_cost * v * X
    else:
        supply = float(((p + t.markup) * v * imp).sum())
        feed_in = -float(((p - t.feed_in_fee) * _dynamic_feed_in_factor(t) * exp).sum())

    energy_tax = t.energy_tax * v * taxed
    fixed = (t.standing_charge + t.grid_fee) * v
    reduction = -t.tax_reduction * v
    total = supply + feed_in + energy_tax + fixed + reduction
    return {
        "import_kwh": I,
        "export_kwh": X,
        "supply": supply,
        "feed_in": feed_in,
        "energy_tax": energy_tax,
        "fixed_charges": fixed,
        "tax_reduction": reduction,
        "total": total,
    }


def unverified_values(cfg: dict, prefix: str = "") -> list[str]:
    """Dotted keys of all entries with verified: false (for README / website)."""
    out = []
    for k, v in cfg.items():
        if isinstance(v, dict) and "value" in v:
            if not v.get("verified", False):
                out.append(f"{prefix}{k}")
        elif isinstance(v, dict):
            out.extend(unverified_values(v, f"{prefix}{k}."))
    return out
