import { computeAll, type Settings } from "./compute";
import type { PriceFile, ProfileFile } from "./model/household";
import type { TariffConfig } from "./model/tariffs";

export interface Request {
  id: number;
  prices: PriceFile;
  profiles: ProfileFile;
  tariffs: TariffConfig;
  settings: Settings;
}

self.onmessage = (ev: MessageEvent<Request>) => {
  const { id, prices, profiles, tariffs, settings } = ev.data;
  try {
    self.postMessage({ id, output: computeAll(prices, profiles, tariffs, settings) });
  } catch (e) {
    self.postMessage({ id, error: String(e) });
  }
};
