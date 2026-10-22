import { describe, expect, it } from "vitest";
import { optimise, STEP } from "../src/model/dp";
import { dayStartsFromOffsets, localHours, socTrajectory } from "../src/model/household";
import { run } from "../src/model/scenario";
import type { Battery, Flows, Inputs, TariffParams } from "../src/model/types";
import { eta } from "../src/model/types";
import { yearInputs } from "./helpers";

const TOL = 1e-6;

// small deterministic PRNG so the tests do not depend on Math.random
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function randomInputs(seed: number, nDays = 3, curtail = false): Inputs {
  const r = rng(seed);
  const n = 24 * nDays;
  const prices = new Float64Array(n);
  const load = new Float64Array(n);
  const solar = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const h = i % 24;
    prices[i] = r() < 0.1 ? -300 * r() : 80 + 60 * Math.sin(((h - 6) / 24) * 2 * Math.PI) + 80 * (r() - 0.5);
    load[i] = 0.2 + r();
    solar[i] = Math.max(0, Math.sin(((h - 6) / 14) * Math.PI)) * 3 * r();
  }
  const dayStarts = Int32Array.from({ length: nDays }, (_, k) => 24 * k);
  return { prices, load, solar, dayStarts, curtail };
}

const base = { vat: 0.21, energy_tax: 0.09, tax_reduction: 0, grid_fee: 0, standing_charge: 0 };
const fixed = (netting: boolean): TariffParams => ({
  ...base, contract: "fixed", netting, supply_price: 0.13, feed_in_compensation: 0.12, feed_in_cost: 0.1,
  markup: 0, feed_in_fee: 0, feed_in_vat_while_netting: false,
});
const dynamic = (netting: boolean): TariffParams => ({
  ...base, contract: "dynamic", netting, supply_price: 0, feed_in_compensation: 0, feed_in_cost: 0,
  markup: 0.02, feed_in_fee: 0.01, feed_in_vat_while_netting: false,
});

const BATTERY: Battery = { capacity_kwh: 5, power_kw: 2.5, round_trip_efficiency: 0.9, degradation_eur_per_kwh: 0.01 };

function checkInvariants(inp: Inputs, f: Flows, b: Battery) {
  for (let t = 0; t < inp.load.length; t++) {
    const lhs = inp.load[t] + f.charge[t] + f.export[t];
    const rhs = inp.solar[t] - f.curtailed[t] + f.discharge[t] + f.import[t];
    expect(Math.abs(lhs - rhs)).toBeLessThan(TOL);
    for (const k of ["import", "export", "curtailed", "charge", "discharge"] as const) {
      expect(f[k][t]).toBeGreaterThanOrEqual(-TOL);
    }
    expect(Math.min(f.charge[t], f.discharge[t])).toBeLessThan(TOL);
    expect(f.charge[t]).toBeLessThanOrEqual(b.power_kw + TOL);
    expect(f.discharge[t]).toBeLessThanOrEqual(b.power_kw + TOL);
  }
  const e = socTrajectory(f.charge, f.discharge, b);
  expect(Math.min(...e)).toBeGreaterThanOrEqual(-TOL);
  expect(Math.max(...e)).toBeLessThanOrEqual(b.capacity_kwh + TOL);
}

describe("day boundaries", () => {
  it("handles 23- and 25-hour days", () => {
    const spring = dayStartsFromOffsets(95, [[0, 1], [26, 2]]);
    expect(Array.from(spring.slice(1)).map((v, i) => v - spring[i])).toEqual([24, 23, 24]);
    const autumn = dayStartsFromOffsets(97, [[0, 2], [27, 1]]);
    expect(Array.from(autumn.slice(1)).map((v, i) => v - autumn[i])).toEqual([24, 25, 24]);
  });

  it("local hours skip 02:00 in spring and repeat it in autumn", () => {
    const spring = localHours(30, [[0, 1], [26, 2]]);
    expect(Array.from(spring.slice(24, 28))).toEqual([0, 1, 3, 4]);
    const autumn = localHours(30, [[0, 2], [27, 1]]);
    expect(Array.from(autumn.slice(24, 29))).toEqual([0, 1, 2, 2, 3]);
  });
});

describe("invariants", () => {
  for (const seed of [1, 2, 3])
    for (const make of [fixed, dynamic])
      for (const netting of [false, true])
        for (const curtail of [false, true])
          it(`seed ${seed} ${make(netting).contract} netting=${netting} curtail=${curtail}`, () => {
            const inp = randomInputs(seed, 3, curtail);
            const t = make(netting);
            const res = Object.fromEntries(
              (["none", "rule", "optimal"] as const).map((s) => [s, run(s, t, inp, BATTERY)]),
            );
            for (const r of Object.values(res)) checkInvariants(inp, r.flows, BATTERY);
            expect(res.optimal.total).toBeLessThanOrEqual(res.none.total + TOL);
          });
});

describe("DP", () => {
  it("is at least as good as the best path through the SOC grid (exhaustive search)", () => {
    const b: Battery = { capacity_kwh: 0.4, power_kw: 0.3, round_trip_efficiency: 0.81, degradation_eur_per_kwh: 0.01 };
    const h = eta(b);
    for (const seed of [11, 12, 13]) {
      const r = rng(seed);
      const T = 6;
      const inp: Inputs = {
        prices: new Float64Array(T),
        load: Float64Array.from({ length: T }, () => r()),
        solar: Float64Array.from({ length: T }, () => r()),
        dayStarts: Int32Array.from([0]),
        curtail: false,
      };
      const buy = Float64Array.from({ length: T }, () => 0.4 * r() - 0.05);
      const sell = Float64Array.from(buy, (v) => v - 0.2 * r());
      const cost = (c: number[], d: number[]) => {
        let s = 0;
        for (let t = 0; t < T; t++) {
          const g = inp.load[t] - inp.solar[t] + c[t] - d[t];
          s += (g > 0 ? buy[t] * g : sell[t] * g) + b.degradation_eur_per_kwh * d[t];
        }
        return s;
      };
      // terminal value: zero for this check by making min(buy) negative
      buy[0] = -1;
      const sched = optimise(inp, b, buy, sell, 0);
      const dpCost = cost(Array.from(sched.charge), Array.from(sched.discharge));

      const N = Math.round(b.capacity_kwh / STEP);
      let best = Infinity;
      const path = new Array<number>(T).fill(0);
      const recurse = (t: number) => {
        if (t === T) {
          const c: number[] = [];
          const d: number[] = [];
          let prev = 0;
          for (const k of path) {
            const dE = (k - prev) * STEP;
            c.push(dE > 0 ? dE / h : 0);
            d.push(dE < 0 ? -dE * h : 0);
            prev = k;
          }
          if (c.every((v) => v <= b.power_kw + 1e-9) && d.every((v) => v <= b.power_kw + 1e-9)) {
            best = Math.min(best, cost(c, d));
          }
          return;
        }
        for (let k = 0; k <= N; k++) {
          path[t] = k;
          recurse(t + 1);
        }
      };
      recurse(0);
      // the DP also uses off-grid (zero grid flow) moves, so it can only be
      // better; the comparison with the continuous optimum is the MILP cross-check
      expect(dpCost).toBeLessThanOrEqual(best + 1e-9);
      const e = socTrajectory(sched.charge, sched.discharge, b);
      expect(Math.min(...e)).toBeGreaterThanOrEqual(-TOL);
      expect(Math.max(...e)).toBeLessThanOrEqual(b.capacity_kwh + TOL);
      expect(Math.max(...sched.charge, ...sched.discharge)).toBeLessThanOrEqual(b.power_kw + TOL);
    }
  });

  it("never charges and discharges at once, even at negative prices", () => {
    const inp = randomInputs(5, 2);
    inp.prices.fill(-400);
    const r = run("optimal", dynamic(false), inp, BATTERY);
    checkInvariants(inp, r.flows, BATTERY);
  });

  it("runs a full year in under one second", () => {
    const inp = yearInputs(2025, 3500, 3000, true);
    const b: Battery = { capacity_kwh: 10, power_kw: 5, round_trip_efficiency: 0.9, degradation_eur_per_kwh: 0.02 };
    const buy = new Float64Array(inp.prices.length);
    const sell = new Float64Array(inp.prices.length);
    for (let i = 0; i < buy.length; i++) {
      buy[i] = (inp.prices[i] / 1000 + 0.02) * 1.21 + 0.11;
      sell[i] = inp.prices[i] / 1000 - 0.015;
    }
    optimise(inp, b, buy, sell); // warm up the JIT
    const t0 = performance.now();
    optimise(inp, b, buy, sell);
    const ms = performance.now() - t0;
    console.log(`DP full year, 10 kWh / 5 kW: ${ms.toFixed(0)} ms`);
    expect(ms).toBeLessThan(process.env.CI ? 3000 : 1000);
  });
});
