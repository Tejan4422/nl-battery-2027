"""Invariants and small exact cases for the household model and the MILP optimiser."""

import numpy as np
import pytest

from model import baselines, lp_optimiser
from model.household import Battery, Inputs, day_starts_from_offsets, simulate, soc_trajectory
from model.scenario import run
from model.tariffs import CONSUMER, TariffParams, annual_bill, marginal_prices

TOL = 1e-6


def make_inputs(prices, load, solar, curtail=False, day_len=24):
    n = len(prices)
    return Inputs(np.asarray(prices, float), np.asarray(load, float), np.asarray(solar, float),
                  np.arange(0, n, day_len), curtail)


def dynamic(netting=False):
    return TariffParams(contract="dynamic", netting=netting, vat=0.21, energy_tax=0.09, tax_reduction=0,
                        grid_fee=0, standing_charge=0, markup=0.02, feed_in_fee=0.01)


def fixed(netting=False):
    return TariffParams(contract="fixed", netting=netting, vat=0.21, energy_tax=0.09, tax_reduction=0,
                        grid_fee=0, standing_charge=0, supply_price=0.13, feed_in_compensation=0.12,
                        feed_in_cost=0.10)


def random_days(seed, days=3):
    rng = np.random.default_rng(seed)
    n = 24 * days
    h = np.arange(n) % 24
    prices = 80 + 60 * np.sin((h - 6) / 24 * 2 * np.pi) + rng.normal(0, 40, n)
    prices[rng.random(n) < 0.1] = rng.uniform(-300, -1)  # negative-price hours
    load = 0.2 + rng.gamma(2, 0.2, n)
    solar = np.clip(np.sin((h - 6) / 14 * np.pi), 0, None) * rng.uniform(0, 3, n)
    return prices, load, solar


BATTERY = Battery(capacity_kwh=5.0, power_kw=2.5, round_trip_efficiency=0.9, degradation_eur_per_kwh=0.01)


def check_invariants(inputs, flows, battery):
    c, d = flows["charge"], flows["discharge"]
    # energy balance every hour
    lhs = inputs.load + c + flows["export"]
    rhs = inputs.solar - flows["curtailed"] + d + flows["import"]
    assert np.max(np.abs(lhs - rhs)) < TOL
    # flows non-negative, power limits
    for k in ("import", "export", "curtailed", "charge", "discharge"):
        assert flows[k].min() >= -TOL, k
    assert c.max() <= battery.power_kw + TOL and d.max() <= battery.power_kw + TOL
    assert np.all(np.minimum(c, d) < TOL), "simultaneous charge and discharge"
    assert np.all(flows["curtailed"] <= inputs.solar + TOL)
    # state of charge within bounds
    e = soc_trajectory(c, d, battery)
    assert e.min() >= -TOL and e.max() <= battery.capacity_kwh + TOL


class TestDayStarts:
    def test_normal_year(self):
        assert list(day_starts_from_offsets(72, [[0, 1]])) == [0, 24, 48]

    def test_dst_days(self):
        # spring: offset +1 -> +2 at index 26 (local 03:00 on day 2), day 2 has 23 hours
        starts = day_starts_from_offsets(24 * 4 - 1, [[0, 1], [26, 2]])
        assert list(np.diff(starts)) == [24, 23, 24]
        # autumn: +2 -> +1, day 2 has 25 hours
        starts = day_starts_from_offsets(24 * 4 + 1, [[0, 2], [27, 1]])
        assert list(np.diff(starts)) == [24, 25, 24]


class TestRuleBased:
    def test_hand_case(self):
        b = Battery(2.0, 1.0, round_trip_efficiency=0.81)  # eta = 0.9
        inp = make_inputs([0] * 4, load=[0, 0, 1, 1], solar=[2, 2, 0, 0])
        c, d = baselines.rule_based(inp, b)
        # hour 0: charge 1 (power limit) -> e = 0.9; hour 1: charge 1 -> e = 1.8
        # hour 2: discharge 1 -> e = 1.8 - 1/0.9 = 0.6889; hour 3: discharge 0.6889*0.9 = 0.62
        assert c.tolist() == pytest.approx([1, 1, 0, 0])
        assert d.tolist() == pytest.approx([0, 0, 1, 0.62])

    def test_capacity_limit(self):
        b = Battery(1.0, 5.0, round_trip_efficiency=1.0)
        inp = make_inputs([0] * 2, load=[0, 0], solar=[3, 3])
        c, _ = baselines.rule_based(inp, b)
        assert c.tolist() == pytest.approx([1, 0])


class TestMilp:
    def test_simple_arbitrage(self):
        """Two hours, no load or solar: buy at 0.10, sell back at a high price."""
        b = Battery(1.0, 1.0, round_trip_efficiency=1.0)
        buy = np.array([0.10, 1.00])
        sell = np.array([0.05, 0.50])
        c, d, e_end = lp_optimiser.optimise_window(np.zeros(2), np.zeros(2), buy, sell,
                                                   np.zeros(2, bool), b, 0.0)
        assert c.tolist() == pytest.approx([1, 0]) and d.tolist() == pytest.approx([0, 1])

    def test_no_burning_at_negative_price(self):
        """At a negative buy price an LP would charge and discharge at once; the MILP must not."""
        b = Battery(10.0, 2.0, round_trip_efficiency=0.81)
        buy, sell = np.array([-0.2]), np.array([-0.3])
        c, d, _ = lp_optimiser.optimise_window(np.zeros(1), np.zeros(1), buy, sell, np.zeros(1, bool), b, 0.0)
        assert min(c[0], d[0]) < TOL
        assert c[0] == pytest.approx(2.0)  # charge at full power to absorb paid energy

    @pytest.mark.parametrize("seed", range(4))
    def test_matches_grid_search(self, seed):
        """Exact search over a 0.01 kWh SOC grid on an 8-hour window (backward
        induction, equivalent to enumerating every path). The MILP is
        continuous, so it must be at least as good and very close."""
        rng = np.random.default_rng(seed)
        T = 8
        b = Battery(1.0, 0.5, round_trip_efficiency=0.81, degradation_eur_per_kwh=0.01)
        load, solar = rng.uniform(0, 1, T), rng.uniform(0, 1, T)
        buy = rng.uniform(-0.05, 0.4, T)
        sell = buy - rng.uniform(0, 0.2, T)

        def cost(c, d):
            g = load - solar + c - d
            return float(np.sum(np.where(g > 0, buy * g, sell * g)) + b.degradation_eur_per_kwh * d.sum())

        c, d, _ = lp_optimiser.optimise_window(load, solar, buy, sell, np.zeros(T, bool), b, 0.0)
        milp_cost = cost(c, d)

        grid = np.linspace(0, 1, 101)
        de = grid[None, :] - grid[:, None]  # [from, to]
        cc = np.where(de > 0, de / b.eta, 0.0)
        dd = np.where(de < 0, -de * b.eta, 0.0)
        feasible = (cc <= b.power_kw + 1e-9) & (dd <= b.power_kw + 1e-9)
        value = np.zeros(len(grid))
        for t in reversed(range(T)):
            g = load[t] - solar[t] + cc - dd
            step = np.where(g > 0, buy[t] * g, sell[t] * g) + b.degradation_eur_per_kwh * dd
            value = np.where(feasible, step + value[None, :], np.inf).min(axis=1)
        best = value[0]
        assert milp_cost <= best + 1e-9
        assert milp_cost == pytest.approx(best, abs=0.01)


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("make", [fixed, dynamic])
@pytest.mark.parametrize("netting", [False, True])
@pytest.mark.parametrize("curtail", [False, True])
def test_invariants_and_never_worse(seed, make, netting, curtail):
    prices, load, solar = random_days(seed)
    inp = make_inputs(prices, load, solar, curtail)
    tariff = make(netting)
    results = {s: run(s, tariff, inp, BATTERY) for s in ("none", "rule", "optimal")}
    for r in results.values():
        check_invariants(inp, r.flows, BATTERY)
    assert results["optimal"].total <= results["none"].total + 1e-6
    assert results["optimal"].total <= results["rule"].total + 1e-6


def test_curtailment_only_at_negative_value():
    prices = np.array([-200.0, 50.0])
    inp = make_inputs(prices, load=[0, 0], solar=[2, 2], curtail=True)
    t = dynamic(False)
    _, sell = marginal_prices(t, prices, CONSUMER)
    f = simulate(inp, np.zeros(2), np.zeros(2), sell)
    assert f["curtailed"].tolist() == [2, 0]
    assert f["export"].tolist() == [0, 2]


def test_netting_regime_switch():
    """A household with much more solar than load must end up in the exporter regime."""
    prices, load, solar = random_days(1)
    inp = make_inputs(prices, load * 0.2, solar * 3)
    r = run("none", fixed(True), inp, BATTERY)
    assert r.regime == "exporter" and r.consistent
    assert r.bill["energy_tax"] == 0.0


def test_bill_matches_marginal_cost_without_netting():
    """Without netting the bill is linear: total energy cost equals the sum of
    hourly marginal costs."""
    prices, load, solar = random_days(2)
    inp = make_inputs(prices, load, solar)
    t = dynamic(False)
    buy, sell = marginal_prices(t, prices)
    r = run("optimal", t, inp, BATTERY)
    hourly = float(np.sum(buy * r.flows["import"] - sell * r.flows["export"]))
    assert r.bill["total"] == pytest.approx(hourly)
