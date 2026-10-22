// Port of model/household.py.
//
// Energy balance every hour:  load + charge + export = (solar - curtailed) + discharge + import
// Battery:                    e[t+1] = e[t] + eta * charge - discharge / eta

import type { Battery, Flows, Inputs } from "./types";
import { eta } from "./types";

export interface PriceFile {
  start_utc: string;
  utc_offsets: [number, number][];
  prices: number[];
}

export interface ProfileFile {
  start_utc: string;
  load: number[];
  solar: number[];
}

/** Local-midnight hour indices. Hour 0 is local midnight. */
export function dayStartsFromOffsets(n: number, utcOffsets: [number, number][]): Int32Array {
  const offset = new Int32Array(n);
  for (const [idx, off] of utcOffsets) offset.fill(off, idx);
  const starts: number[] = [];
  for (let i = 0; i < n; i++) {
    const localHour = (((i + offset[i] - offset[0]) % 24) + 24) % 24;
    if (localHour === 0) starts.push(i);
  }
  return Int32Array.from(starts);
}

/** Local clock hour of each index (for charts). */
export function localHours(n: number, utcOffsets: [number, number][]): Int32Array {
  const out = new Int32Array(n);
  let off = utcOffsets[0][1];
  let next = 1;
  const off0 = off;
  for (let i = 0; i < n; i++) {
    if (next < utcOffsets.length && utcOffsets[next][0] === i) off = utcOffsets[next++][1];
    out[i] = (((i + off - off0) % 24) + 24) % 24;
  }
  return out;
}

function scaled(shape: number[], total: number): Float64Array {
  let sum = 0;
  for (const v of shape) sum += v;
  const out = new Float64Array(shape.length);
  for (let i = 0; i < shape.length; i++) out[i] = (shape[i] / sum) * total;
  return out;
}

export function buildInputs(
  prices: PriceFile,
  profiles: ProfileFile,
  annualLoadKwh: number,
  annualSolarKwh: number,
  curtail: boolean,
): Inputs {
  if (prices.start_utc !== profiles.start_utc) {
    throw new Error("price and profile files do not start at the same hour");
  }
  return {
    prices: Float64Array.from(prices.prices),
    load: scaled(profiles.load, annualLoadKwh),
    solar: scaled(profiles.solar, annualSolarKwh),
    dayStarts: dayStartsFromOffsets(prices.prices.length, prices.utc_offsets),
    curtail,
  };
}

export function days(inputs: Inputs): [number, number][] {
  const n = inputs.prices.length;
  const out: [number, number][] = [];
  for (let k = 0; k < inputs.dayStarts.length; k++) {
    out.push([inputs.dayStarts[k], k + 1 < inputs.dayStarts.length ? inputs.dayStarts[k + 1] : n]);
  }
  return out;
}

export function simulate(
  inputs: Inputs,
  charge: Float64Array,
  discharge: Float64Array,
  sell: Float64Array,
): Flows {
  const n = inputs.load.length;
  const imp = new Float64Array(n);
  const exp = new Float64Array(n);
  const cur = new Float64Array(n);
  for (let t = 0; t < n; t++) {
    const net = inputs.load[t] - inputs.solar[t] + charge[t] - discharge[t];
    if (net > 0) imp[t] = net;
    else {
      const x = -net;
      const u = inputs.curtail && sell[t] < 0 ? Math.min(x, inputs.solar[t]) : 0;
      exp[t] = x - u;
      cur[t] = u;
    }
  }
  return { import: imp, export: exp, curtailed: cur, charge, discharge };
}

/** State of charge at the start of each hour plus the final value (length n + 1). */
export function socTrajectory(charge: Float64Array, discharge: Float64Array, b: Battery, e0 = 0): Float64Array {
  const h = eta(b);
  const e = new Float64Array(charge.length + 1);
  e[0] = e0;
  for (let t = 0; t < charge.length; t++) e[t + 1] = e[t] + h * charge[t] - discharge[t] / h;
  return e;
}
