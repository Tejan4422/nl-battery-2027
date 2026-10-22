"""Run a strategy under a tariff and report the annual cost.

Netting makes the annual bill depend on whether total import ends up above or
below total export. Each strategy is therefore run with the marginal prices of
the 'consumer' regime first; if the result turns out to be a net exporter it
is re-run with 'exporter' prices. If neither run is consistent with its own
assumption (the optimum sits at import = export) the cheaper one is reported.
Without netting there is only one regime.
"""

from dataclasses import dataclass

import numpy as np

from . import baselines, lp_optimiser
from .household import Battery, Inputs, simulate
from .tariffs import CONSUMER, EXPORTER, TariffParams, annual_bill, marginal_prices

STRATEGIES = ("none", "rule", "optimal")


@dataclass
class Result:
    strategy: str
    regime: str
    consistent: bool
    bill: dict
    degradation: float
    flows: dict

    @property
    def total(self) -> float:
        return self.bill["total"] + self.degradation


def schedule(strategy: str, inputs: Inputs, battery: Battery, buy, sell, lookahead_days: int = 1):
    if strategy == "none":
        return baselines.no_battery(inputs)
    if strategy == "rule":
        return baselines.rule_based(inputs, battery)
    if strategy == "optimal":
        return lp_optimiser.optimise(inputs, battery, buy, sell, lookahead_days)
    raise ValueError(strategy)


def run(strategy: str, tariff: TariffParams, inputs: Inputs, battery: Battery,
        lookahead_days: int = 1, schedule_fn=schedule) -> Result:
    regimes = (CONSUMER, EXPORTER) if tariff.netting else (CONSUMER,)
    tried: list[Result] = []
    for regime in regimes:
        buy, sell = marginal_prices(tariff, inputs.prices, regime)
        c, d = schedule_fn(strategy, inputs, battery, buy, sell, lookahead_days)
        flows = simulate(inputs, c, d, sell)
        bill = annual_bill(tariff, inputs.prices, flows["import"], flows["export"])
        consumer = bill["import_kwh"] >= bill["export_kwh"]
        consistent = not tariff.netting or consumer == (regime == CONSUMER)
        deg = battery.degradation_eur_per_kwh * float(np.sum(d))
        r = Result(strategy, regime, consistent, bill, deg, flows)
        if consistent:
            return r
        tried.append(r)
    return min(tried, key=lambda r: r.total)
