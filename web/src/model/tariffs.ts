// Port of model/tariffs.py. See that file for the reasoning behind each formula.

import type { Bill, Contract, Regime, TariffParams } from "./types";

export interface TariffEntry {
  value: number | boolean;
  unit: string;
  source: string;
  as_of: string;
  verified?: boolean;
  placeholder?: boolean;
  note?: string;
}

// Shape of config/tariffs_nl.yaml (exported to public/data/tariffs.json)
export interface TariffConfig {
  vat: TariffEntry;
  energy_tax: Record<string, TariffEntry>;
  tax_reduction: Record<string, TariffEntry>;
  grid_fee: TariffEntry;
  fixed_contract: Record<string, TariffEntry>;
  dynamic_contract: Record<string, TariffEntry>;
  rules: Record<string, { netting: boolean; tax_year: string }>;
}

const num = (e: TariffEntry): number => Number(e.value);

export function paramsFromConfig(cfg: TariffConfig, contract: Contract, rules: string): TariffParams {
  const r = cfg.rules[rules];
  const ty = r.tax_year;
  const base: TariffParams = {
    contract,
    netting: r.netting,
    vat: num(cfg.vat),
    energy_tax: num(cfg.energy_tax[ty]),
    tax_reduction: num(cfg.tax_reduction[ty]),
    grid_fee: num(cfg.grid_fee),
    standing_charge: 0,
    supply_price: 0,
    feed_in_compensation: 0,
    feed_in_cost: 0,
    markup: 0,
    feed_in_fee: 0,
    feed_in_vat_while_netting: false,
  };
  if (contract === "fixed") {
    const c = cfg.fixed_contract;
    return {
      ...base,
      standing_charge: num(c.standing_charge),
      supply_price: num(c.supply_price),
      feed_in_compensation: num(c.feed_in_compensation),
      feed_in_cost: num(c.feed_in_cost),
    };
  }
  const c = cfg.dynamic_contract;
  return {
    ...base,
    standing_charge: num(c.standing_charge),
    markup: num(c.markup),
    feed_in_fee: num(c.feed_in_fee),
    feed_in_vat_while_netting: Boolean(c.feed_in_vat_while_netting.value),
  };
}

const dynamicFeedInFactor = (t: TariffParams): number =>
  t.netting && t.feed_in_vat_while_netting ? 1 + t.vat : 1;

/** Hourly marginal cost of importing (buy) and value of exporting (sell), EUR/kWh. */
export function marginalPrices(
  t: TariffParams,
  priceEurMwh: Float64Array,
  regime: Regime = "consumer",
): { buy: Float64Array; sell: Float64Array } {
  const n = priceEurMwh.length;
  const buy = new Float64Array(n);
  const sell = new Float64Array(n);
  const v = 1 + t.vat;
  if (t.contract === "fixed") {
    const retail = (t.supply_price + t.energy_tax) * v;
    const feedCost = t.feed_in_cost * v;
    let b: number, s: number;
    if (t.netting && regime === "consumer") [b, s] = [retail, retail - feedCost];
    else if (t.netting) [b, s] = [t.feed_in_compensation, t.feed_in_compensation - feedCost];
    else [b, s] = [retail, t.feed_in_compensation - feedCost];
    buy.fill(b);
    sell.fill(s);
    return { buy, sell };
  }
  const k = dynamicFeedInFactor(t);
  const tax = t.energy_tax * v;
  const taxBuy = !t.netting || regime === "consumer" ? tax : 0;
  const taxSell = t.netting && regime === "consumer" ? tax : 0;
  for (let i = 0; i < n; i++) {
    const p = priceEurMwh[i] / 1000;
    buy[i] = (p + t.markup) * v + taxBuy;
    sell[i] = (p - t.feed_in_fee) * k + taxSell;
  }
  return { buy, sell };
}

/** Exact annual bill (EUR incl. VAT) from hourly import and export (kWh). */
export function annualBill(
  t: TariffParams,
  priceEurMwh: Float64Array,
  imp: Float64Array,
  exp: Float64Array,
): Bill {
  const v = 1 + t.vat;
  let I = 0;
  let X = 0;
  for (let i = 0; i < imp.length; i++) {
    I += imp[i];
    X += exp[i];
  }
  const taxed = t.netting ? Math.max(I - X, 0) : I;
  let supply: number;
  let feedIn: number;
  if (t.contract === "fixed") {
    if (t.netting) {
      supply = t.supply_price * v * Math.max(I - X, 0);
      feedIn = -t.feed_in_compensation * Math.max(X - I, 0);
    } else {
      supply = t.supply_price * v * I;
      feedIn = -t.feed_in_compensation * X;
    }
    feedIn += t.feed_in_cost * v * X;
  } else {
    const k = dynamicFeedInFactor(t);
    supply = 0;
    feedIn = 0;
    for (let i = 0; i < imp.length; i++) {
      const p = priceEurMwh[i] / 1000;
      supply += (p + t.markup) * v * imp[i];
      feedIn -= (p - t.feed_in_fee) * k * exp[i];
    }
  }
  const energyTax = t.energy_tax * v * taxed;
  const fixed = (t.standing_charge + t.grid_fee) * v;
  const reduction = -t.tax_reduction * v;
  return {
    import_kwh: I,
    export_kwh: X,
    supply,
    feed_in: feedIn,
    energy_tax: energyTax,
    fixed_charges: fixed,
    tax_reduction: reduction,
    total: supply + feedIn + energyTax + fixed + reduction,
  };
}
