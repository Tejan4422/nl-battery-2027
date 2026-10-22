# Phase 2: reinforcement learning for heat pump and EV

Not implemented yet. This folder is a placeholder.

## Idea

Phase 1 optimises a home battery with perfect day-ahead foresight, which is a
well-posed deterministic problem (DP / LP). Heat pumps and electric vehicles add
things that make that approach awkward:

- **Thermal state.** A heat pump's flexibility depends on indoor temperature,
  the building's thermal mass and the weather forecast. Comfort is a soft
  constraint, not a hard bound.
- **Uncertain availability.** An EV is only flexible while it is plugged in, and
  arrival time, departure time and required charge vary from day to day.
- **Forecast error.** Outdoor temperature and solar output are forecasts, not
  known values.

The plan is to train an RL agent (likely PPO or SAC on a small custom Gymnasium
environment) that controls heat pump setpoint, EV charging rate and battery
power together, with the Phase 1 DP schedule as a benchmark for the battery-only
part.

## Planned components

- `env/`: Gymnasium environment reusing the Phase 1 tariff and household model
  (prices, solar, base load), plus a simple RC thermal model of the house and an
  EV availability model.
- `agents/`: training scripts and baselines (rule-based thermostat, "charge EV
  on arrival", "charge EV in cheapest hours" with perfect foresight).
- `eval/`: comparison against the perfect-foresight upper bound, with
  out-of-sample years for validation.

## Later: multiple homes

Coordinating many homes behind one transformer to reduce peak load (grid
congestion is a real constraint in large parts of the Netherlands).
