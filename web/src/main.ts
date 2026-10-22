import "./style.css";
import { CONTRACTS, RULES, type Output, type Rules, type Settings } from "./compute";
import type { PriceFile, ProfileFile } from "./model/household";
import type { TariffConfig, TariffEntry } from "./model/tariffs";
import type { Contract } from "./model/types";
import { dayTable, renderDay, renderMonthly } from "./ui/charts";
import { type Lang, STRINGS, type Strings } from "./ui/i18n";

const YEARS = [2025, 2024];
const KWH_PER_KWP = 875;
// set by the Pages workflow from the repository name
const REPO_URL = import.meta.env.VITE_REPO_URL ?? "https://github.com/";

// tariff fields exposed in the UI: [dotted path in config, label key]
const TARIFF_FIELDS: [string, keyof Strings][] = [
  ["fixed_contract.supply_price", "fixedSupply"],
  ["fixed_contract.feed_in_compensation", "fixedFeedIn"],
  ["fixed_contract.feed_in_cost", "fixedFeedCost"],
  ["dynamic_contract.markup", "dynMarkup"],
  ["dynamic_contract.feed_in_fee", "dynFee"],
  ["energy_tax.2026", "tax2026"],
  ["energy_tax.2027", "tax2027"],
];

interface State {
  lang: Lang;
  year: number;
  load: number;
  solar: number;
  capacity: number;
  power: number;
  rte: number;
  degradation: number;
  batteryPrice: number;
  curtail: boolean;
  tariffs: Record<string, number>;
  view: { rules: Rules; contract: Contract; season: "summer" | "winter" };
}

const DEFAULTS: Omit<State, "lang" | "tariffs"> = {
  year: 2025,
  load: 3500,
  solar: 3000,
  capacity: 5,
  power: 2.5,
  rte: 90,
  degradation: 0.02,
  batteryPrice: 4000,
  curtail: true,
  view: { rules: "2027", contract: "dynamic", season: "summer" },
};

const storedLang = (() => {
  try {
    return localStorage.getItem("lang") as Lang | null;
  } catch {
    return null;
  }
})();

const state: State = { ...DEFAULTS, view: { ...DEFAULTS.view }, lang: storedLang ?? "nl", tariffs: {} };
let tariffConfig: TariffConfig;
let output: Output | null = null;
let errorMsg = "";
let busy = false;
const cache = new Map<string, unknown>();

const $ = (id: string) => document.getElementById(id)!;
const t = () => STRINGS[state.lang];
const eur = (v: number, d = 0) =>
  new Intl.NumberFormat(state.lang === "nl" ? "nl-NL" : "en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);

function getPath(cfg: TariffConfig, path: string): TariffEntry {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return path.split(".").reduce((n: any, k) => n[k], cfg);
}

async function fetchJson<T>(path: string): Promise<T> {
  if (cache.has(path)) return cache.get(path) as T;
  const res = await fetch(`${import.meta.env.BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  const json = (await res.json()) as T;
  cache.set(path, json);
  return json;
}

// ---- computation (in a worker) ----

const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
let requestId = 0;
worker.onmessage = (ev: MessageEvent<{ id: number; output?: Output; error?: string }>) => {
  if (ev.data.id !== requestId) return; // a newer request is pending
  busy = false;
  if (ev.data.error) {
    errorMsg = ev.data.error;
    output = null;
  } else {
    errorMsg = "";
    output = ev.data.output!;
  }
  renderResults();
};

function settings(): Settings {
  return {
    annualLoadKwh: state.load,
    annualSolarKwh: state.solar,
    battery: {
      capacity_kwh: state.capacity,
      power_kw: state.power,
      round_trip_efficiency: state.rte / 100,
      degradation_eur_per_kwh: state.degradation,
    },
    curtail: state.curtail,
    tariffOverrides: state.tariffs,
  };
}

let timer: number | undefined;
function schedule() {
  window.clearTimeout(timer);
  timer = window.setTimeout(compute, 250);
}

async function compute() {
  const valid = state.load > 0 && state.solar >= 0 && state.capacity > 0 && state.power > 0 && state.rte > 0 && state.rte <= 100;
  if (!valid) return;
  busy = true;
  renderStatus();
  const [prices, profiles] = await Promise.all([
    fetchJson<PriceFile>(`data/prices_nl_${state.year}.json`),
    fetchJson<ProfileFile>(`data/profiles_nl_${state.year}.json`),
  ]);
  worker.postMessage({ id: ++requestId, prices, profiles, tariffs: tariffConfig, settings: settings() });
}

// ---- inputs ----

function numberField(id: string, label: string, value: number, step: number, min = 0, max?: number): string {
  return `<div class="field"><label for="${id}">${label}</label>` +
    `<input id="${id}" type="number" inputmode="decimal" step="${step}" min="${min}"${max != null ? ` max="${max}"` : ""} value="${value}"></div>`;
}

function renderInputs() {
  const s = t();
  const tariffInputs = TARIFF_FIELDS.map(([path, key]) =>
    numberField(`tariff-${path}`, s[key] as string, state.tariffs[path], 0.001, -1),
  ).join("");
  $("inputs").innerHTML = `
    <h2>${s.inputs}</h2>
    <fieldset>
      <legend>${s.household}</legend>
      <div class="field"><label for="year">${s.priceYear}</label>
        <select id="year">${YEARS.map((y) => `<option${y === state.year ? " selected" : ""}>${y}</option>`).join("")}</select></div>
      ${numberField("load", s.consumption, state.load, 100)}
      ${numberField("solar", s.solar, state.solar, 100)}
      ${numberField("kwp", s.kwp, +(state.solar / KWH_PER_KWP).toFixed(2), 0.1)}
      <p class="hint">${s.kwpHint}</p>
    </fieldset>
    <fieldset>
      <legend>${s.battery}</legend>
      ${numberField("capacity", s.capacity, state.capacity, 0.5, 0.1, 50)}
      ${numberField("power", s.power, state.power, 0.1, 0.1, 25)}
      ${numberField("rte", s.rte, state.rte, 1, 50, 100)}
      ${numberField("degradation", s.degradation, state.degradation, 0.01)}
      ${numberField("batteryPrice", s.batteryPrice, state.batteryPrice, 100)}
      <label class="check"><input id="curtail" type="checkbox"${state.curtail ? " checked" : ""}> ${s.curtail}</label>
    </fieldset>
    <details>
      <summary>${s.tariffs}</summary>
      <p class="hint">${s.tariffsHint}</p>
      ${tariffInputs}
      <button type="button" class="link-btn" id="reset">${s.reset}</button>
    </details>`;

  const bind = (id: keyof State, after?: () => void) => {
    const el = $(id) as HTMLInputElement;
    el.addEventListener("input", () => {
      const v = parseFloat(el.value);
      if (!Number.isFinite(v)) return;
      (state as unknown as Record<string, number>)[id] = v;
      after?.();
      schedule();
    });
  };
  bind("load");
  bind("solar", () => ((($("kwp") as HTMLInputElement).value = (state.solar / KWH_PER_KWP).toFixed(2))));
  bind("capacity");
  bind("power");
  bind("rte");
  bind("degradation");
  bind("batteryPrice", () => renderResults());
  $("kwp").addEventListener("input", (e) => {
    const v = parseFloat((e.target as HTMLInputElement).value);
    if (!Number.isFinite(v)) return;
    state.solar = Math.round(v * KWH_PER_KWP);
    ($("solar") as HTMLInputElement).value = String(state.solar);
    schedule();
  });
  $("year").addEventListener("change", (e) => {
    state.year = Number((e.target as HTMLSelectElement).value);
    schedule();
  });
  $("curtail").addEventListener("change", (e) => {
    state.curtail = (e.target as HTMLInputElement).checked;
    schedule();
  });
  for (const [path] of TARIFF_FIELDS) {
    const el = $(`tariff-${path}`) as HTMLInputElement;
    el.addEventListener("input", () => {
      const v = parseFloat(el.value);
      if (!Number.isFinite(v)) return;
      state.tariffs[path] = v;
      schedule();
    });
  }
  $("reset").addEventListener("click", () => {
    resetTariffs();
    renderInputs();
    schedule();
  });
}

function resetTariffs() {
  state.tariffs = Object.fromEntries(TARIFF_FIELDS.map(([p]) => [p, Number(getPath(tariffConfig, p).value)]));
}

// ---- results ----

function renderStatus() {
  const el = document.querySelector<HTMLElement>("#status");
  if (!el) return;
  const s = t();
  el.classList.toggle("error", !!errorMsg);
  el.textContent = errorMsg ? `${s.error} ${errorMsg}` : busy ? s.calculating : output ? s.computedIn(output.ms) : "";
}

const contractLabel = (c: Contract) => (c === "fixed" ? t().fixed : t().dynamic);
const rulesLabel = (r: Rules) => (r === "2026" ? t().rules2026 : t().rules2027);

function seg<T extends string>(name: string, options: [T, string][], current: T): string {
  return `<div class="seg" role="group" data-seg="${name}">${options
    .map(([v, l]) => `<button type="button" data-value="${v}" aria-pressed="${v === current}">${l}</button>`)
    .join("")}</div>`;
}

function payback(saving: number): string {
  const s = t();
  return saving > 0 ? s.years(state.batteryPrice / saving) : s.never;
}

function renderResults() {
  const s = t();
  const root = $("results");
  if (!output) {
    root.innerHTML = `<div class="card"><h2>${s.results}</h2><p id="status" class="status"></p></div>`;
    renderStatus();
    return;
  }
  const o = output;
  const saving = (r: Rules, c: Contract, k: "rule" | "optimal") => o.table[r][c].none.total - o.table[r][c][k].total;

  const stat = (c: Contract) => {
    const v = saving("2027", c, "optimal");
    return `<div class="stat"><div class="label">${contractLabel(c)} · ${s.optimal}</div>
      <div class="value">EUR ${eur(v)}</div>
      <div class="sub">${s.perYear} · ${s.payback.toLowerCase()} ${payback(v)}</div></div>`;
  };

  // one compact table per set of rules, side by side on wide screens and
  // stacked on phones, so the 2026/2027 comparison never needs side-scrolling
  const header = (r: Rules) =>
    `<caption>${rulesLabel(r)}</caption><thead><tr><th></th>${CONTRACTS.map((c) => `<th>${c === "fixed" ? s.fixedShort : s.dynamicShort}</th>`).join("")}</tr></thead>`;
  const costTable = (r: Rules) => {
    const rows = (["none", "rule", "optimal"] as const)
      .map((k) => {
        const cells = CONTRACTS.map((c) => {
          const v = o.table[r][c][k].total;
          const best = Math.min(...(["none", "rule", "optimal"] as const).map((q) => o.table[r][c][q].total));
          return `<td class="${Math.abs(v - best) < 0.005 ? "best" : ""}">${eur(v)}</td>`;
        }).join("");
        return `<tr><td class="label-cell">${s[k]}</td>${cells}</tr>`;
      })
      .join("");
    return `<div class="table-wrap"><table>${header(r)}<tbody>${rows}</tbody></table></div>`;
  };
  const savingTable = (r: Rules) => {
    const rows = (["rule", "optimal"] as const)
      .map(
        (k) =>
          `<tr><td class="label-cell">${s[k]}</td>${CONTRACTS.map((c) => {
            const v = saving(r, c, k);
            return `<td>EUR ${eur(v)}<br><span class="muted">${payback(v)}</span></td>`;
          }).join("")}</tr>`,
      )
      .join("");
    return `<div class="table-wrap"><table>${header(r)}<tbody>${rows}</tbody></table></div>`;
  };

  const v = state.view;
  const chart = o.charts[v.rules][v.contract];
  const day = chart[v.season];

  root.innerHTML = `
    <div class="card">
      <h2>${s.headline("2027")}</h2>
      <div class="stats">${stat("dynamic")}${stat("fixed")}</div>
      <p id="status" class="status" style="margin-top:.6rem"></p>
    </div>
    <div class="card">
      <h2>${s.annualCost}</h2>
      <div class="pair">${RULES.map(costTable).join("")}</div>
      <p class="note"><b>${s.rule}</b>: ${s.ruleLong}. <b>${s.optimal}</b>: ${s.optimalLong}.</p>
    </div>
    <div class="card">
      <h2>${s.savings}</h2>
      <p class="note" style="margin:0 0 .5rem">${s.savingVsNone} · ${s.payback.toLowerCase()} (EUR ${eur(state.batteryPrice)})</p>
      <div class="pair">${RULES.map(savingTable).join("")}</div>
      <p class="note">${s.paybackNote}</p>
    </div>
    <div class="card">
      <h2>${s.dayCharts}</h2>
      <div class="controls">
        ${seg("season", [["summer", s.summer], ["winter", s.winter]], v.season)}
        ${seg("rules", RULES.map((r) => [r, r] as [Rules, string]), v.rules)}
        ${seg("contract", CONTRACTS.map((c) => [c, contractLabel(c)] as [Contract, string]), v.contract)}
      </div>
      <p class="note" style="margin-top:0">${s.dayNote(day.date)}</p>
      <div id="day"></div>
      <details style="margin-top:.75rem"><summary>${s.showTable}</summary>${dayTable(day, s)}</details>
    </div>
    <div class="card">
      <h2>${s.monthly} · ${contractLabel(v.contract)} · ${v.rules}</h2>
      <div id="monthly"></div>
      <p class="note">${s.monthlyNote}</p>
    </div>`;

  root.querySelectorAll<HTMLElement>("[data-seg]").forEach((g) =>
    g.addEventListener("click", (e) => {
      const btn = (e.target as HTMLElement).closest("button");
      if (!btn) return;
      (state.view as Record<string, string>)[g.dataset.seg!] = btn.dataset.value!;
      renderResults();
    }),
  );
  renderStatus();
  drawCharts();
}

function drawCharts() {
  if (!output) return;
  const v = state.view;
  const chart = output.charts[v.rules][v.contract];
  renderDay($("day"), chart[v.season], t());
  renderMonthly($("monthly"), chart.monthlySaving.rule, chart.monthlySaving.optimal, t());
}

// ---- assumptions ----

function flatten(cfg: TariffConfig): [string, TariffEntry][] {
  const out: [string, TariffEntry][] = [];
  const walk = (node: Record<string, unknown>, prefix: string) => {
    for (const [k, v] of Object.entries(node)) {
      if (k === "rules") continue;
      if (v && typeof v === "object" && "value" in v) out.push([prefix + k, v as TariffEntry]);
      else if (v && typeof v === "object") walk(v as Record<string, unknown>, `${prefix}${k}.`);
    }
  };
  walk(cfg as unknown as Record<string, unknown>, "");
  return out;
}

function renderAssumptions() {
  const s = t();
  const rows = flatten(tariffConfig)
    .map(([key, e]) => {
      const value =
        typeof e.value === "boolean"
          ? String(e.value)
          : e.unit === "fraction"
            ? `${(Number(e.value) * 100).toFixed(0)}%`
            : `${e.value} ${e.unit.replace("EUR/", "EUR / ")}`;
      let host = e.source;
      try {
        host = new URL(e.source).hostname.replace(/^www\./, "");
      } catch {
        /* keep raw */
      }
      const status = e.verified
        ? `<span class="badge ok">${s.verified}</span>`
        : `<span class="badge">${e.placeholder ? s.placeholder : s.unverified}</span>`;
      return `<tr><td>${s.tariffNames[key] ?? key}${e.note ? `<br><span class="muted">${e.note}</span>` : ""}</td>
        <td class="num">${value}</td><td><a href="${e.source}" rel="noopener" target="_blank">${host}</a></td>
        <td class="num">${e.as_of}</td><td>${status}</td></tr>`;
    })
    .join("");
  $("assumptions").innerHTML = `
    <h2>${s.assumptions}</h2>
    <ul>${s.modelAssumptions.map((a) => `<li>${a}</li>`).join("")}</ul>
    <p class="note" style="margin-bottom:.5rem">${s.assumptionsLead}</p>
    <div class="table-wrap"><table><thead><tr><th>${s.colItem}</th><th>${s.colValue}</th><th>${s.colSource}</th>
      <th>${s.colAsOf}</th><th>${s.colStatus}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ---- page ----

function renderStatic() {
  const s = t();
  document.documentElement.lang = state.lang;
  document.title = state.lang === "nl" ? "Thuisbatterij na 2027" : "Home battery after 2027";
  $("title").textContent = s.title;
  $("lead").textContent = s.lead;
  $("disclaimer").textContent = s.disclaimer;
  $("footer").innerHTML = `${s.footer} <a href="${REPO_URL}" rel="noopener">${s.sourceCode}</a>`;
  document.querySelectorAll<HTMLButtonElement>("[data-lang]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.lang === state.lang)),
  );
}

function renderAll() {
  renderStatic();
  renderInputs();
  renderResults();
  renderAssumptions();
}

document.querySelectorAll<HTMLButtonElement>("[data-lang]").forEach((b) =>
  b.addEventListener("click", () => {
    state.lang = b.dataset.lang as Lang;
    try {
      localStorage.setItem("lang", state.lang);
    } catch {
      /* private mode */
    }
    renderAll();
  }),
);

let resizeTimer: number | undefined;
let lastWidth = window.innerWidth;
window.addEventListener("resize", () => {
  if (window.innerWidth === lastWidth) return; // ignore mobile address-bar height changes
  lastWidth = window.innerWidth;
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(drawCharts, 150);
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawCharts);

(async () => {
  try {
    tariffConfig = await fetchJson<TariffConfig>("data/tariffs.json");
  } catch (e) {
    errorMsg = String(e);
    renderStatic();
    renderResults();
    return;
  }
  resetTariffs();
  renderAll();
  compute();
})();
