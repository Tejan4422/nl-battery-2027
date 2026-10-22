import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { buildInputs, type PriceFile, type ProfileFile } from "../src/model/household";
import type { Battery, Inputs, TariffParams } from "../src/model/types";

export const ROOT = resolve(__dirname, "../..");

export const readJson = <T>(path: string): T => JSON.parse(readFileSync(resolve(ROOT, path), "utf8")) as T;

export interface Expected {
  total: number;
  bill_total: number;
  energy_cost: number;
  import_kwh: number;
  export_kwh: number;
  regime: string;
  consistent: boolean;
}

export interface Fixture {
  name: string;
  description: string;
  inputs:
    | { prices: number[]; load: number[]; solar: number[]; day_starts: number[]; curtail: boolean }
    | { year: number; annual_load_kwh: number; annual_solar_kwh: number; curtail: boolean };
  battery: Battery;
  tariff: TariffParams;
  lookahead_days: number;
  expected: Record<"none" | "rule" | "optimal", Expected>;
}

export function yearInputs(year: number, load: number, solar: number, curtail: boolean): Inputs {
  const prices = readJson<PriceFile>(`web/public/data/prices_nl_${year}.json`);
  const profiles = readJson<ProfileFile>(`web/public/data/profiles_nl_${year}.json`);
  return buildInputs(prices, profiles, load, solar, curtail);
}

export function fixtureInputs(fx: Fixture): Inputs {
  const i = fx.inputs;
  if ("year" in i) return yearInputs(i.year, i.annual_load_kwh, i.annual_solar_kwh, i.curtail);
  return {
    prices: Float64Array.from(i.prices),
    load: Float64Array.from(i.load),
    solar: Float64Array.from(i.solar),
    dayStarts: Int32Array.from(i.day_starts),
    curtail: i.curtail,
  };
}
