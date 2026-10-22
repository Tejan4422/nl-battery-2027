"""Reference battery optimiser: one mixed-integer linear programme per window.

This is the slow, exact counterpart of the dynamic programme in the browser
(web/src/model/dp.ts) and is used to validate it.

Per hour t in a window of T hours:
    variables  c, d, i, x, u >= 0, e (state of charge after hour t), z, w in {0, 1}
    minimise   sum buy*i - sell*x + deg*d
    subject to i - x - c + d - u = L - S                (energy balance)
               e[t] = e[t-1] + eta*c - d/eta,  e[-1] = e0 (battery)
               c <= P*z,  d <= P*(1 - z)                 (no simultaneous charge/discharge)
               i <= (L+P)*w,  x <= (S+P)*(1 - w)         (no simultaneous import/export)
               0 <= e <= E,  0 <= u <= S where curtailment is allowed, else u = 0

The binaries matter. With negative prices an LP relaxation would charge and
discharge at once to burn energy through conversion losses, which a real
battery cannot do and the DP does not allow. And in hours where the sell price
exceeds the buy price (possible under netting at very negative prices) it
would import and export at once without limit. The DP works on the net grid
flow per hour, so it has neither option.

Rolling horizon: day k is optimised together with day k+1 (`lookahead_days`)
and only day k is committed. The day-ahead auction publishes tomorrow's prices
around 13:00, so this assumes perfect foresight of at most ~35 hours.
"""

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_array, diags, eye, hstack, vstack

from .household import Battery, Inputs, curtailable


def optimise_window(load, solar, buy, sell, can_curtail, battery: Battery, e0: float):
    """Optimal (c, d, e_end) for one window. All arrays have length T."""
    T = len(load)
    eta, P, E = battery.eta, battery.power_kw, battery.capacity_kwh
    I = eye(T, format="csr")
    Z = csr_array((T, T))
    m_imp = diags(load + P, format="csr")
    m_exp = diags(solar + P, format="csr")
    # variable order: c, d, i, x, u, e, z, w
    balance = hstack([-I, I, I, -I, -I, Z, Z, Z])
    shift = diags([np.ones(T), -np.ones(T - 1)], [0, -1], format="csr")  # e[t] - e[t-1]
    soc = hstack([-eta * I, I / eta, Z, Z, Z, shift, Z, Z])
    cz = hstack([I, Z, Z, Z, Z, Z, -P * I, Z])
    dz = hstack([Z, I, Z, Z, Z, Z, P * I, Z])
    iw = hstack([Z, Z, I, Z, Z, Z, Z, -m_imp])
    xw = hstack([Z, Z, Z, I, Z, Z, Z, m_exp])
    A = vstack([balance, soc, cz, dz, iw, xw]).tocsr()
    rhs_soc = np.zeros(T)
    rhs_soc[0] = e0
    ninf = np.full(T, -np.inf)
    lb = np.r_[load - solar, rhs_soc, ninf, ninf, ninf, ninf]
    ub = np.r_[load - solar, rhs_soc, np.zeros(T), np.full(T, P), np.zeros(T), solar + P]

    u_max = np.where(can_curtail, solar, 0.0)
    lo = np.zeros(8 * T)
    hi = np.r_[np.full(T, P), np.full(T, P), load + P, solar + P, u_max,
               np.full(T, E), np.ones(T), np.ones(T)]
    cost = np.r_[np.zeros(T), np.full(T, battery.degradation_eur_per_kwh), buy, -sell,
                 np.zeros(T), np.zeros(T), np.zeros(T), np.zeros(T)]
    integrality = np.r_[np.zeros(6 * T), np.ones(2 * T)]

    res = milp(cost, constraints=LinearConstraint(A, lb, ub), bounds=Bounds(lo, hi),
               integrality=integrality, options={"mip_rel_gap": 1e-9})
    if not res.success:
        raise RuntimeError(f"MILP failed: {res.message}")
    v = res.x
    c, d, e = v[:T], v[T:2 * T], v[5 * T:6 * T]
    # clean solver noise so downstream invariant checks are exact
    c, d = np.clip(c, 0, P), np.clip(d, 0, P)
    return c, d, float(np.clip(e[-1], 0, E))


def optimise(inputs: Inputs, battery: Battery, buy: np.ndarray, sell: np.ndarray,
             lookahead_days: int = 1, e0: float = 0.0):
    """Rolling-horizon schedule (c, d) for the whole period."""
    n = len(inputs.load)
    c, d = np.zeros(n), np.zeros(n)
    can_curtail = curtailable(inputs, sell)
    days = inputs.days
    e = e0
    for k, (a, b) in enumerate(days):
        end = days[min(k + lookahead_days, len(days) - 1)][1]
        cw, dw, _ = optimise_window(inputs.load[a:end], inputs.solar[a:end], buy[a:end], sell[a:end],
                                    can_curtail[a:end], battery, e)
        h = b - a
        c[a:b], d[a:b] = cw[:h], dw[:h]
        e = float(np.clip(e + np.sum(battery.eta * c[a:b] - d[a:b] / battery.eta), 0, battery.capacity_kwh))
    return c, d
