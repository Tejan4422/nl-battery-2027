"""Strategies that do not look at prices."""

import numpy as np

from .household import Battery, Inputs


def no_battery(inputs: Inputs, battery: Battery | None = None):
    n = len(inputs.load)
    return np.zeros(n), np.zeros(n)


def rule_based(inputs: Inputs, battery: Battery, e0: float = 0.0):
    """Charge from solar surplus, discharge to cover consumption. No grid charging,
    no export from the battery."""
    n = len(inputs.load)
    c, d = np.zeros(n), np.zeros(n)
    eta, cap, p = battery.eta, battery.capacity_kwh, battery.power_kw
    e = e0
    net = inputs.load - inputs.solar
    for t in range(n):
        if net[t] < 0:
            c[t] = min(-net[t], p, (cap - e) / eta)
            e += eta * c[t]
        elif net[t] > 0:
            d[t] = min(net[t], p, e * eta)
            e -= d[t] / eta
        e = min(max(e, 0.0), cap)  # guard against float drift
    return c, d
