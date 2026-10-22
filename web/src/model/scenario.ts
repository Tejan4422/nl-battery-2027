// Port of model/scenario.py: run a strategy, resolving the netting regime.

import { noBattery, ruleBased } from "./baselines";
import { optimise } from "./dp";
import { simulate } from "./household";
import { annualBill, marginalPrices } from "./tariffs";
import type { Battery, Bill, Flows, Inputs, Regime, Schedule, Strategy, TariffParams } from "./types";

export interface Result {
  strategy: Strategy;
  regime: Regime;
  consistent: boolean;
  bill: Bill;
  degradation: number;
  total: number;
  flows: Flows;
  sell: Float64Array;
}

export function schedule(
  strategy: Strategy,
  inputs: Inputs,
  b: Battery,
  buy: Float64Array,
  sell: Float64Array,
  lookaheadDays: number,
): Schedule {
  if (strategy === "none") return noBattery(inputs);
  if (strategy === "rule") return ruleBased(inputs, b);
  return optimise(inputs, b, buy, sell, lookaheadDays);
}

export function run(strategy: Strategy, t: TariffParams, inputs: Inputs, b: Battery, lookaheadDays = 1): Result {
  const regimes: Regime[] = t.netting ? ["consumer", "exporter"] : ["consumer"];
  const tried: Result[] = [];
  for (const regime of regimes) {
    const { buy, sell } = marginalPrices(t, inputs.prices, regime);
    const s = schedule(strategy, inputs, b, buy, sell, lookaheadDays);
    const flows = simulate(inputs, s.charge, s.discharge, sell);
    const bill = annualBill(t, inputs.prices, flows.import, flows.export);
    const consumer = bill.import_kwh >= bill.export_kwh;
    const consistent = !t.netting || consumer === (regime === "consumer");
    let dis = 0;
    for (const v of s.discharge) dis += v;
    const degradation = b.degradation_eur_per_kwh * dis;
    const r: Result = { strategy, regime, consistent, bill, degradation, total: bill.total + degradation, flows, sell };
    if (consistent) return r;
    tried.push(r);
  }
  return tried.reduce((x, y) => (y.total < x.total ? y : x));
}
