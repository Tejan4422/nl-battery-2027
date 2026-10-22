// Battery optimiser for the browser: dynamic programming over a discretised
// state of charge, with the same rolling horizon and terminal value as the
// Python MILP (model/lp_optimiser.py), which is used to validate it.
//
// Value function: V_t(e) on a grid e = j * STEP, j = 0..N, linearly
// interpolated in between. Per hour the candidate actions are
//   - moves to neighbouring grid points (limited by the power rating), and
//   - "kink" moves: charge exactly the solar surplus or discharge exactly the
//     deficit (zero grid flow), and when curtailment is allowed, the point
//     where export equals solar output.
// The hourly cost is piecewise linear in the battery power with kinks at
// those points, so together these contain the best continuous action up to
// the interpolation error. Without the kink moves the battery could only
// absorb surplus in 0.1 kWh blocks, which costs a few percent of the saving.
//
// The backward pass evaluates states on the grid; the forward pass then
// follows the actual (continuous) state of charge. Because the DP works on the
// net grid flow per hour it can never charge and discharge, or import and
// export, in the same hour.

import { days } from "./household";
import type { Battery, Inputs, Schedule } from "./types";
import { eta } from "./types";

export const STEP = 0.1; // kWh

/** Value per kWh stored at the end of a window. Must match lp_optimiser.terminal_value. */
export function terminalValue(buy: Float64Array, sell: Float64Array, a: number, end: number, b: Battery): number {
  let minBuy = Infinity;
  let minSell = Infinity;
  for (let t = a; t < end; t++) {
    if (buy[t] < minBuy) minBuy = buy[t];
    if (sell[t] < minSell) minSell = sell[t];
  }
  const mid = (minBuy + Math.max(minSell, 0)) / 2;
  return eta(b) * Math.max(0, mid - b.degradation_eur_per_kwh);
}

export function optimise(
  inputs: Inputs,
  b: Battery,
  buy: Float64Array,
  sell: Float64Array,
  lookaheadDays = 1,
  e0 = 0,
): Schedule {
  const n = inputs.load.length;
  const charge = new Float64Array(n);
  const discharge = new Float64Array(n);
  const h = eta(b);
  const P = b.power_kw;
  const N = Math.floor(b.capacity_kwh / STEP + 1e-9);
  const E = N * STEP;
  const kUp = Math.min(N, Math.floor((P * h) / STEP + 1e-9));
  const kDown = Math.min(N, Math.floor(P / h / STEP + 1e-9));
  const K = kUp + kDown + 1; // grid moves -kDown..kUp, index m = k + kDown
  const deg = b.degradation_eur_per_kwh;
  const dEmax = P * h; // largest stored-energy increase in one hour
  const dEmin = -P / h; // largest decrease

  const moveC = new Float64Array(K);
  const moveD = new Float64Array(K);
  for (let m = 0; m < K; m++) {
    const dE = (m - kDown) * STEP;
    if (dE > 0) moveC[m] = dE / h;
    else if (dE < 0) moveD[m] = -dE * h;
  }

  const ds = days(inputs);
  const maxWindow = 24 * (lookaheadDays + 1) + 2;
  const stepCost = new Float64Array(maxWindow * K);
  const kinks = new Float64Array(maxWindow * 2); // stored-energy change of each kink move
  const vRows = new Float64Array(26 * (N + 1)); // V_{t+1} for the committed hours
  let vNext = new Float64Array(N + 1);
  let vCur = new Float64Array(N + 1);
  let e = Math.min(E, Math.max(0, e0));

  // cost of one hour for a given AC charge c / discharge d
  const hourCost = (i: number, c: number, d: number): number => {
    const g = inputs.load[i] - inputs.solar[i] + c - d;
    if (g >= 0) return buy[i] * g + deg * d;
    const x = inputs.curtail && sell[i] < 0 ? Math.max(-g - inputs.solar[i], 0) : -g;
    return -sell[i] * x + deg * d;
  };
  const interp = (v: Float64Array, x: number): number => {
    const f = Math.min(Math.max(x / STEP, 0), N);
    const j = Math.min(Math.floor(f), N - 1);
    if (N === 0) return v[0];
    const w = f - j;
    return v[j] * (1 - w) + v[j + 1] * w;
  };
  const toCD = (dE: number): [number, number] => (dE >= 0 ? [dE / h, 0] : [0, -dE * h]);

  for (let day = 0; day < ds.length; day++) {
    const [a, dayEnd] = ds[day];
    const end = ds[Math.min(day + lookaheadDays, ds.length - 1)][1];
    const T = end - a;
    const H = dayEnd - a;

    for (let t = 0; t < T; t++) {
      const i = a + t;
      for (let m = 0; m < K; m++) stepCost[t * K + m] = hourCost(i, moveC[m], moveD[m]);
      // battery power b = c - d that makes grid flow zero, and (curtailment) export = solar
      const zeroFlow = inputs.solar[i] - inputs.load[i];
      const exportAll = -inputs.load[i];
      kinks[2 * t] = zeroFlow >= 0 ? zeroFlow * h : zeroFlow / h;
      kinks[2 * t + 1] = inputs.curtail && sell[i] < 0 ? exportAll / h : NaN;
    }

    const tv = terminalValue(buy, sell, a, end, b);
    for (let j = 0; j <= N; j++) vNext[j] = -tv * j * STEP;

    for (let t = T - 1; t >= 0; t--) {
      if (t < H) vRows.set(vNext, t * (N + 1));
      const off = t * K;
      const i = a + t;
      for (let j = 0; j <= N; j++) {
        // idle first so that ties resolve to doing nothing
        let best = stepCost[off + kDown] + vNext[j];
        const mLo = Math.max(0, kDown - j);
        const mHi = Math.min(K - 1, kDown + N - j);
        for (let m = mLo; m <= mHi; m++) {
          const v = stepCost[off + m] + vNext[j + m - kDown];
          if (v < best) best = v;
        }
        const ej = j * STEP;
        for (let q = 0; q < 2; q++) {
          const dE = kinks[2 * t + q];
          if (!(dE >= dEmin && dE <= dEmax) || ej + dE < 0 || ej + dE > E) continue;
          const [c, d] = toCD(dE);
          const v = hourCost(i, c, d) + interp(vNext, ej + dE);
          if (v < best) best = v;
        }
        vCur[j] = best;
      }
      [vCur, vNext] = [vNext, vCur];
    }

    // forward pass from the actual state of charge
    for (let t = 0; t < H; t++) {
      const i = a + t;
      const v1 = vRows.subarray(t * (N + 1), (t + 1) * (N + 1));
      let bestDE = 0;
      let best = hourCost(i, 0, 0) + interp(v1, e);
      const consider = (dE: number) => {
        if (!(dE >= dEmin - 1e-12 && dE <= dEmax + 1e-12)) return;
        const target = e + dE;
        if (target < -1e-12 || target > E + 1e-12) return;
        const [c, d] = toCD(dE);
        const v = hourCost(i, c, d) + interp(v1, target);
        if (v < best - 1e-12) {
          best = v;
          bestDE = dE;
        }
      };
      const jLo = Math.max(0, Math.ceil((e + dEmin) / STEP - 1e-9));
      const jHi = Math.min(N, Math.floor((e + dEmax) / STEP + 1e-9));
      for (let j = jLo; j <= jHi; j++) consider(j * STEP - e);
      consider(kinks[2 * t]);
      if (!Number.isNaN(kinks[2 * t + 1])) consider(kinks[2 * t + 1]);
      // full power within bounds, in case the grid points do not reach it
      consider(Math.min(dEmax, E - e));
      consider(Math.max(dEmin, -e));

      const [c, d] = toCD(bestDE);
      charge[i] = Math.min(c, P);
      discharge[i] = Math.min(d, P);
      e = Math.min(E, Math.max(0, e + bestDE));
    }
  }
  return { charge, discharge };
}
