// Mirrors the Python dataclasses in model/tariffs.py and model/household.py.
// Field names stay in snake_case so fixtures can be passed straight through.

export type Contract = "fixed" | "dynamic";
export type Regime = "consumer" | "exporter";
export type Strategy = "none" | "rule" | "optimal";

export interface TariffParams {
  contract: Contract;
  netting: boolean;
  vat: number;
  energy_tax: number;
  tax_reduction: number;
  grid_fee: number;
  standing_charge: number;
  supply_price: number;
  feed_in_compensation: number;
  feed_in_cost: number;
  markup: number;
  feed_in_fee: number;
  feed_in_vat_while_netting: boolean;
}

export interface Battery {
  capacity_kwh: number;
  power_kw: number;
  round_trip_efficiency: number;
  degradation_eur_per_kwh: number;
}

export interface Inputs {
  prices: Float64Array; // EUR/MWh
  load: Float64Array; // kWh
  solar: Float64Array; // kWh
  dayStarts: Int32Array; // hour index of each local midnight, starting with 0
  curtail: boolean;
}

export interface Flows {
  import: Float64Array;
  export: Float64Array;
  curtailed: Float64Array;
  charge: Float64Array;
  discharge: Float64Array;
}

export interface Bill {
  import_kwh: number;
  export_kwh: number;
  supply: number;
  feed_in: number;
  energy_tax: number;
  fixed_charges: number;
  tax_reduction: number;
  total: number;
}

export interface Schedule {
  charge: Float64Array;
  discharge: Float64Array;
}

export const eta = (b: Battery): number => Math.sqrt(b.round_trip_efficiency);
