"""Tariff tests against hand-computed small cases.

The parameters are round numbers chosen so the expected values can be checked
on paper; they are not the real Dutch tariffs.
"""

import numpy as np
import pytest

from model.tariffs import (
    CONSUMER,
    EXPORTER,
    TariffParams,
    annual_bill,
    load_yaml,
    marginal_prices,
    params_from_config,
    unverified_values,
)

COMMON = dict(vat=0.25, energy_tax=0.10, tax_reduction=400.0, grid_fee=300.0, standing_charge=100.0)
# fixed charges incl. VAT: (100 + 300) * 1.25 - 400 * 1.25 = 0
FIXED = dict(contract="fixed", supply_price=0.10, feed_in_compensation=0.05, feed_in_cost=0.02, **COMMON)
DYN = dict(contract="dynamic", markup=0.02, feed_in_fee=0.01, **COMMON)


def fixed(netting):
    return TariffParams(netting=netting, **FIXED)


def dynamic(netting, vat_on_feed_in=False):
    return TariffParams(netting=netting, feed_in_vat_while_netting=vat_on_feed_in, **DYN)


PRICES = np.array([100.0, -50.0, 200.0])  # EUR/MWh -> 0.10, -0.05, 0.20 EUR/kWh


class TestFixedBill:
    # retail incl. VAT = (0.10 + 0.10) * 1.25 = 0.25; feed-in cost incl. VAT = 0.025

    def test_no_netting(self):
        b = annual_bill(fixed(False), PRICES, [2, 0, 1], [0, 4, 0])
        # supply 0.125*3 = 0.375, tax 0.125*3 = 0.375, feed-in -0.05*4 + 0.025*4 = -0.1
        assert b["supply"] == pytest.approx(0.375)
        assert b["energy_tax"] == pytest.approx(0.375)
        assert b["feed_in"] == pytest.approx(-0.1)
        assert b["fixed_charges"] + b["tax_reduction"] == pytest.approx(0.0)
        assert b["total"] == pytest.approx(0.65)

    def test_netting_consumer(self):
        # I = 3, X = 2: net 1 kWh at 0.25, feed-in cost 2 * 0.025
        b = annual_bill(fixed(True), PRICES, [2, 0, 1], [0, 2, 0])
        assert b["total"] == pytest.approx(0.25 + 0.05)

    def test_netting_exporter(self):
        # I = 3, X = 5: surplus 2 kWh paid 0.05, feed-in cost 5 * 0.025
        b = annual_bill(fixed(True), PRICES, [2, 0, 1], [0, 5, 0])
        assert b["energy_tax"] == 0.0
        assert b["total"] == pytest.approx(-0.10 + 0.125)

    def test_netting_never_worse_than_no_netting(self):
        rng = np.random.default_rng(0)
        for _ in range(50):
            imp, exp = rng.uniform(0, 3, 24), rng.uniform(0, 3, 24)
            p = rng.uniform(-100, 300, 24)
            for t in (fixed, dynamic):
                assert annual_bill(t(True), p, imp, exp)["total"] <= annual_bill(t(False), p, imp, exp)["total"] + 1e-12


class TestDynamicBill:
    def test_no_netting(self):
        b = annual_bill(dynamic(False), PRICES, [2, 0, 1], [0, 4, 0])
        # supply: (0.12*2 + 0.22*1) * 1.25 = 0.575
        # feed-in: -(-0.05 - 0.01) * 4 = +0.24 (negative price: feeding in costs money)
        # tax: 0.125 * 3 = 0.375
        assert b["supply"] == pytest.approx(0.575)
        assert b["feed_in"] == pytest.approx(0.24)
        assert b["energy_tax"] == pytest.approx(0.375)
        assert b["total"] == pytest.approx(1.19)

    def test_netting_tax_on_net_only(self):
        b = annual_bill(dynamic(True), PRICES, [2, 0, 1], [0, 2, 0])
        assert b["energy_tax"] == pytest.approx(0.125 * 1)

    def test_netting_feed_in_vat(self):
        exp = [1, 0, 0]  # 1 kWh at 0.10 - 0.01 = 0.09
        b = annual_bill(dynamic(True, vat_on_feed_in=True), PRICES, [0, 0, 0], exp)
        assert b["feed_in"] == pytest.approx(-0.09 * 1.25)
        b = annual_bill(dynamic(False, vat_on_feed_in=True), PRICES, [0, 0, 0], exp)
        assert b["feed_in"] == pytest.approx(-0.09)


class TestMarginalPrices:
    def test_fixed(self):
        buy, sell = marginal_prices(fixed(False), PRICES)
        assert buy.tolist() == pytest.approx([0.25] * 3)
        assert sell.tolist() == pytest.approx([0.025] * 3)
        buy, sell = marginal_prices(fixed(True), PRICES, CONSUMER)
        assert sell.tolist() == pytest.approx([0.225] * 3)
        buy, sell = marginal_prices(fixed(True), PRICES, EXPORTER)
        assert buy.tolist() == pytest.approx([0.05] * 3)
        assert sell.tolist() == pytest.approx([0.025] * 3)

    def test_dynamic(self):
        buy, sell = marginal_prices(dynamic(False), PRICES)
        # (p + 0.02) * 1.25 + 0.10 * 1.25
        assert buy.tolist() == pytest.approx([0.275, 0.0875, 0.40])
        assert sell.tolist() == pytest.approx([0.09, -0.06, 0.19])
        buy, sell = marginal_prices(dynamic(True), PRICES, CONSUMER)
        assert sell.tolist() == pytest.approx([0.215, 0.065, 0.315])
        buy, sell = marginal_prices(dynamic(True), PRICES, EXPORTER)
        assert buy.tolist() == pytest.approx([0.15, -0.0375, 0.275])

    @pytest.mark.parametrize("netting", [False, True])
    @pytest.mark.parametrize("regime", [CONSUMER, EXPORTER])
    @pytest.mark.parametrize("make", [fixed, dynamic])
    def test_marginal_matches_bill(self, make, netting, regime):
        """Finite-difference check: marginal prices equal the bill's slope,
        as long as the perturbation stays within the regime."""
        t = make(netting)
        imp = np.array([3.0, 2.0, 4.0])
        exp = np.array([1.0, 1.5, 0.5]) if regime == CONSUMER else np.array([5.0, 6.0, 4.0])
        buy, sell = marginal_prices(t, PRICES, regime)
        base = annual_bill(t, PRICES, imp, exp)["total"]
        eps = 1e-4
        for h in range(3):
            di = np.zeros(3)
            di[h] = eps
            assert (annual_bill(t, PRICES, imp + di, exp)["total"] - base) / eps == pytest.approx(buy[h], abs=1e-6)
            assert (base - annual_bill(t, PRICES, imp, exp + di)["total"]) / eps == pytest.approx(sell[h], abs=1e-6)

    def test_buy_at_least_sell_with_real_config(self):
        """Needed for the optimisation to be well posed (no profit from
        importing and exporting at the same time)."""
        cfg = load_yaml()
        p = np.linspace(-500, 4000, 200)
        for contract in ("fixed", "dynamic"):
            for rules in ("2026", "2027"):
                for regime in (CONSUMER, EXPORTER):
                    buy, sell = marginal_prices(params_from_config(cfg, contract, rules), p, regime)
                    assert np.all(buy >= sell - 1e-12), (contract, rules, regime)


def test_config_loads_and_has_sources():
    cfg = load_yaml()
    for contract in ("fixed", "dynamic"):
        for rules in ("2026", "2027"):
            params_from_config(cfg, contract, rules)

    def walk(d):
        for v in d.values():
            if isinstance(v, dict) and "value" in v:
                assert v.get("source", "").startswith("http"), v
                assert v.get("as_of"), v
            elif isinstance(v, dict):
                walk(v)

    walk(cfg)
    assert "energy_tax.2027" in unverified_values(cfg)
