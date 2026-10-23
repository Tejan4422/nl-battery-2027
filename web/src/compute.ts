// Runs every scenario for the website: 2 rules x 2 contracts x 3 strategies,
// plus the data behind the charts. Pure function, executed in a Web Worker.

import { buildInputs, localHours, socTrajectory, type PriceFile, type ProfileFile } from "./model/household";
import { run, type Result } from "./model/scenario";
import { marginalPrices, paramsFromConfig, type TariffConfig } from "./model/tariffs";
import type { Battery, Bill, Contract, Regime, Strategy } from "./model/types";

export const RULES = ["2026", "2027"] as const;
export const CONTRACTS: Contract[] = ["fixed", "dynamic"];
export const STRATEGIES: Strategy[] = ["none", "rule", "optimal"];
export type Rules = (typeof RULES)[number];

export interface Settings {
  annualLoadKwh: number;
  annualSolarKwh: number;
  battery: Battery;
  curtail: boolean;
  // dotted paths into the tariff config, e.g. "fixed_contract.supply_price"
  tariffOverrides: Record<string, number>;
}

export interface ScenarioSummary {
  total: number;
  bill: Bill;
  degradation: number;
  regime: Regime;
  consistent: boolean;
  curtailedKwh: number;
}

export interface DaySeries {
  date: string; // local date, YYYY-MM-DD
  hours: number[]; // local clock hour of each point
  price: number[]; // EUR/MWh
  load: number[];
  solar: number[];
  battery: number[]; // AC power, + charging, - discharging
  soc: number[]; // stored energy at the end of the hour
}

export interface ChartData {
  summer: DaySeries;
  winter: DaySeries;
  monthlySaving: Record<"rule" | "optimal", number[]>; // EUR per month vs no battery, in `months` order
}

export interface Output {
  months: { year: number; month: number }[]; // calendar months of the window, in order
  table: Record<Rules, Record<Contract, Record<Strategy, ScenarioSummary>>>;
  charts: Record<Rules, Record<Contract, ChartData>>;
}

export function applyOverrides(cfg: TariffConfig, overrides: Record<string, number>): TariffConfig {
  const copy = structuredClone(cfg);
  for (const [path, value] of Object.entries(overrides)) {
    const keys = path.split(".");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let node: any = copy;
    for (const k of keys) node = node[k];
    node.value = value;
  }
  return copy;
}

interface Calendar {
  month: Int32Array; // 0..11 per hour
  period: Int32Array; // index into `months` per hour
  months: { year: number; month: number }[];
  dayDate: string[]; // per day
  dayMonth: number[];
}

function calendar(prices: PriceFile, dayStarts: Int32Array): Calendar {
  const n = prices.prices.length;
  const t0 = Date.parse(prices.start_utc);
  const offset = new Int32Array(n);
  for (const [idx, off] of prices.utc_offsets) offset.fill(off, idx);
  const localDate = (i: number) => new Date(t0 + (i + offset[i]) * 3_600_000);
  const month = new Int32Array(n);
  const period = new Int32Array(n);
  const months: { year: number; month: number }[] = [];
  for (let i = 0; i < n; i++) {
    const d = localDate(i);
    month[i] = d.getUTCMonth();
    const last = months[months.length - 1];
    if (!last || last.month !== month[i] || last.year !== d.getUTCFullYear()) {
      months.push({ year: d.getUTCFullYear(), month: month[i] });
    }
    period[i] = months.length - 1;
  }
  const dayDate = Array.from(dayStarts, (s) => localDate(s).toISOString().slice(0, 10));
  const dayMonth = Array.from(dayStarts, (s) => month[s]);
  return { month, period, months, dayDate, dayMonth };
}

/** Day in the given month whose price spread (max - min) is the median of that month. */
function typicalDay(prices: Float64Array, ds: [number, number][], cal: Calendar, month: number): number {
  const candidates = ds
    .map(([a, b], k) => {
      let lo = Infinity;
      let hi = -Infinity;
      for (let i = a; i < b; i++) {
        lo = Math.min(lo, prices[i]);
        hi = Math.max(hi, prices[i]);
      }
      return { k, spread: hi - lo, len: b - a };
    })
    .filter((d) => cal.dayMonth[d.k] === month && d.len === 24)
    .sort((x, y) => x.spread - y.spread);
  return candidates[Math.floor(candidates.length / 2)].k;
}

function daySeries(r: Result, inputs: ReturnType<typeof buildInputs>, b: Battery, hours: Int32Array,
  soc: Float64Array, [a, z]: [number, number], date: string): DaySeries {
  const sl = (x: ArrayLike<number>) => Array.from(x).slice(a, z);
  return {
    date,
    hours: sl(hours),
    price: sl(inputs.prices),
    load: sl(inputs.load),
    solar: sl(inputs.solar),
    battery: sl(r.flows.charge).map((c, i) => c - r.flows.discharge[a + i]),
    soc: Array.from(soc).slice(a + 1, z + 1).map((e) => Math.min(Math.max(e, 0), b.capacity_kwh)),
  };
}

export function computeAll(
  prices: PriceFile,
  profiles: ProfileFile,
  baseCfg: TariffConfig,
  s: Settings,
): Output {
  const cfg = applyOverrides(baseCfg, s.tariffOverrides);
  const inputs = buildInputs(prices, profiles, s.annualLoadKwh, s.annualSolarKwh, s.curtail);
  const n = inputs.prices.length;
  const ds: [number, number][] = Array.from(inputs.dayStarts, (a, k) => [
    a,
    k + 1 < inputs.dayStarts.length ? inputs.dayStarts[k + 1] : n,
  ]);
  const cal = calendar(prices, inputs.dayStarts);
  const hours = localHours(n, prices.utc_offsets);
  const summerDay = typicalDay(inputs.prices, ds, cal, 6);
  const winterDay = typicalDay(inputs.prices, ds, cal, 0);

  const table = {} as Output["table"];
  const charts = {} as Output["charts"];
  for (const rules of RULES) {
    table[rules] = {} as Output["table"][Rules];
    charts[rules] = {} as Output["charts"][Rules];
    for (const contract of CONTRACTS) {
      const t = paramsFromConfig(cfg, contract, rules);
      const res = {} as Record<Strategy, Result>;
      for (const strategy of STRATEGIES) res[strategy] = run(strategy, t, inputs, s.battery);
      table[rules][contract] = Object.fromEntries(
        STRATEGIES.map((k) => {
          const r = res[k];
          let cur = 0;
          for (const v of r.flows.curtailed) cur += v;
          return [k, { total: r.total, bill: r.bill, degradation: r.degradation, regime: r.regime,
            consistent: r.consistent, curtailedKwh: cur }];
        }),
      ) as Record<Strategy, ScenarioSummary>;

      // Monthly split of the saving, using the hourly marginal prices of the
      // optimised run's regime (the annual bill is linear within a regime).
      const { buy, sell } = marginalPrices(t, inputs.prices, res.optimal.regime);
      const deg = s.battery.degradation_eur_per_kwh;
      const monthlyCost = (r: Result) => {
        const m = new Array(cal.months.length).fill(0);
        for (let i = 0; i < n; i++) {
          m[cal.period[i]] += buy[i] * r.flows.import[i] - sell[i] * r.flows.export[i] + deg * r.flows.discharge[i];
        }
        return m;
      };
      const base = monthlyCost(res.none);
      const saving = (r: Result) => monthlyCost(r).map((v, i) => base[i] - v);

      const opt = res.optimal;
      const soc = socTrajectory(opt.flows.charge, opt.flows.discharge, s.battery);
      charts[rules][contract] = {
        summer: daySeries(opt, inputs, s.battery, hours, soc, ds[summerDay], cal.dayDate[summerDay]),
        winter: daySeries(opt, inputs, s.battery, hours, soc, ds[winterDay], cal.dayDate[winterDay]),
        monthlySaving: { rule: saving(res.rule), optimal: saving(opt) },
      };
    }
  }
  return { months: cal.months, table, charts };
}
