// Same hand-computed cases as tests/test_tariffs.py.

import { describe, expect, it } from "vitest";
import { annualBill, marginalPrices, paramsFromConfig, type TariffConfig } from "../src/model/tariffs";
import type { TariffParams } from "../src/model/types";
import { readJson } from "./helpers";

const common = { vat: 0.25, energy_tax: 0.1, tax_reduction: 400, grid_fee: 300, standing_charge: 100 };
const fixed = (netting: boolean): TariffParams => ({
  ...common, contract: "fixed", netting, supply_price: 0.1, feed_in_compensation: 0.05, feed_in_cost: 0.02,
  markup: 0, feed_in_fee: 0, feed_in_vat_while_netting: false,
});
const dynamic = (netting: boolean, vat = false): TariffParams => ({
  ...common, contract: "dynamic", netting, supply_price: 0, feed_in_compensation: 0, feed_in_cost: 0,
  markup: 0.02, feed_in_fee: 0.01, feed_in_vat_while_netting: vat,
});
const P = Float64Array.from([100, -50, 200]);
const f = (a: number[]) => Float64Array.from(a);

describe("bill", () => {
  it("fixed, no netting", () => expect(annualBill(fixed(false), P, f([2, 0, 1]), f([0, 4, 0])).total).toBeCloseTo(0.65, 12));
  it("fixed, netting consumer", () => expect(annualBill(fixed(true), P, f([2, 0, 1]), f([0, 2, 0])).total).toBeCloseTo(0.3, 12));
  it("fixed, netting exporter", () => expect(annualBill(fixed(true), P, f([2, 0, 1]), f([0, 5, 0])).total).toBeCloseTo(0.025, 12));
  it("dynamic, no netting", () => expect(annualBill(dynamic(false), P, f([2, 0, 1]), f([0, 4, 0])).total).toBeCloseTo(1.19, 12));
  it("dynamic, netting with VAT on feed-in", () =>
    expect(annualBill(dynamic(true, true), P, f([0, 0, 0]), f([1, 0, 0])).feed_in).toBeCloseTo(-0.09 * 1.25, 12));
});

describe("marginal prices", () => {
  it("dynamic, no netting", () => {
    const { buy, sell } = marginalPrices(dynamic(false), P);
    [0.275, 0.0875, 0.4].forEach((v, i) => expect(buy[i]).toBeCloseTo(v, 12));
    [0.09, -0.06, 0.19].forEach((v, i) => expect(sell[i]).toBeCloseTo(v, 12));
  });
  it("fixed, netting exporter", () => {
    const { buy, sell } = marginalPrices(fixed(true), P, "exporter");
    expect(buy[0]).toBeCloseTo(0.05, 12);
    expect(sell[0]).toBeCloseTo(0.025, 12);
  });
});

describe("config", () => {
  it("reads the exported YAML the same way as Python", () => {
    const cfg = readJson<TariffConfig>("web/public/data/tariffs.json");
    const t = paramsFromConfig(cfg, "dynamic", "2027");
    expect(t.netting).toBe(false);
    expect(t.energy_tax).toBe(Number(cfg.energy_tax["2027"].value));
    expect(paramsFromConfig(cfg, "fixed", "2026").netting).toBe(true);
  });
});
