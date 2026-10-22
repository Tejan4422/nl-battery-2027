// Port of model/baselines.py: strategies that do not look at prices.

import type { Battery, Inputs, Schedule } from "./types";
import { eta } from "./types";

export function noBattery(inputs: Inputs): Schedule {
  const n = inputs.load.length;
  return { charge: new Float64Array(n), discharge: new Float64Array(n) };
}

/** Charge from solar surplus, discharge to cover consumption. */
export function ruleBased(inputs: Inputs, b: Battery, e0 = 0): Schedule {
  const n = inputs.load.length;
  const charge = new Float64Array(n);
  const discharge = new Float64Array(n);
  const h = eta(b);
  const cap = b.capacity_kwh;
  const p = b.power_kw;
  let e = e0;
  for (let t = 0; t < n; t++) {
    const net = inputs.load[t] - inputs.solar[t];
    if (net < 0) {
      charge[t] = Math.min(-net, p, (cap - e) / h);
      e += h * charge[t];
    } else if (net > 0) {
      discharge[t] = Math.min(net, p, e * h);
      e -= discharge[t] / h;
    }
    e = Math.min(Math.max(e, 0), cap);
  }
  return { charge, discharge };
}
