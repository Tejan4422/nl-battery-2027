import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { DaySeries } from "../compute";
import type { Strings } from "./i18n";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const fmt = (v: number, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "-");

let dayPlots: uPlot[] = [];

function axis(): uPlot.Axis {
  return {
    stroke: css("--text-3"),
    grid: { stroke: css("--grid"), width: 1 },
    ticks: { show: false },
    font: "11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    size: 28,
  };
}

function yAxis(): uPlot.Axis {
  return { ...axis(), size: 44 };
}

/** Three stacked panels (price, energy, battery content) sharing one hour axis. */
export function renderDay(el: HTMLElement, day: DaySeries, s: Strings): void {
  dayPlots.forEach((p) => p.destroy());
  dayPlots = [];
  el.innerHTML = "";
  const width = Math.max(260, el.clientWidth);
  const x = day.hours.map((_, i) => i);
  const hourLabel = (i: number) => `${String(day.hours[Math.round(i)] ?? "").padStart(2, "0")}:00`;
  const sync = uPlot.sync("day");
  const readout = document.createElement("div");
  readout.className = "readout";

  const showReadout = (idx: number | null | undefined) => {
    if (idx == null) {
      readout.textContent = "";
      return;
    }
    readout.textContent =
      `${hourLabel(idx)}  ${s.price} ${fmt(day.price[idx], 1)} EUR/MWh · ${s.load} ${fmt(day.load[idx])} · ` +
      `${s.solarSeries} ${fmt(day.solar[idx])} · ${s.batterySeries.split(" (")[0]} ${fmt(day.battery[idx])} · ` +
      `${s.soc} ${fmt(day.soc[idx])} kWh`;
  };

  const base = (height: number): Omit<uPlot.Options, "series"> => ({
    width,
    height,
    padding: [8, 8, 0, 0],
    cursor: {
      sync: { key: sync.key, setSeries: false },
      points: { size: 8 },
      drag: { x: false, y: false },
    },
    legend: { show: false },
    scales: { x: { time: false, range: [-0.5, x.length - 0.5] } },
    axes: [
      { ...axis(), values: (_u, ticks) => ticks.map((t) => hourLabel(t)), incrs: [3, 6], space: 30 },
      yAxis(),
    ],
    hooks: { setCursor: [(u) => showReadout(u.cursor.idx)] },
  });

  const block = (title: string, legend: [string, string, boolean][]) => {
    const t = document.createElement("div");
    t.className = "chart-title";
    t.textContent = title;
    el.appendChild(t);
    if (legend.length > 1) {
      const lg = document.createElement("div");
      lg.className = "legend";
      for (const [label, color, isBar] of legend) {
        const item = document.createElement("span");
        item.innerHTML = `<i class="swatch${isBar ? " bar" : ""}" style="background:${color}"></i>`;
        item.append(label);
        lg.appendChild(item);
      }
      el.appendChild(lg);
    }
    const c = document.createElement("div");
    c.className = "chart";
    el.appendChild(c);
    return c;
  };

  const ink = css("--text");
  const c1 = css("--series-1");
  const c2 = css("--series-2");
  const c3 = css("--series-3");
  const bars = uPlot.paths.bars!({ size: [0.6, 14], radius: 0.25 });
  const stepped = uPlot.paths.stepped!({ align: 1 });

  const priceEl = block(s.priceChart, []);
  dayPlots.push(
    new uPlot(
      { ...base(130), series: [{}, { label: s.price, stroke: ink, width: 2, paths: stepped, points: { show: false } }] },
      [x, day.price],
      priceEl,
    ),
  );

  const energyEl = block(s.energyChart, [
    [s.load, c1, false],
    [s.solarSeries, c2, false],
    [s.batterySeries, c3, true],
  ]);
  dayPlots.push(
    new uPlot(
      {
        ...base(170),
        series: [
          {},
          { label: s.batterySeries, fill: c3, stroke: c3, width: 0, paths: bars, points: { show: false } },
          { label: s.load, stroke: c1, width: 2, points: { show: false } },
          { label: s.solarSeries, stroke: c2, width: 2, points: { show: false } },
        ],
      },
      [x, day.battery, day.load, day.solar],
      energyEl,
    ),
  );

  const socEl = block(s.socChart, []);
  dayPlots.push(
    new uPlot(
      {
        ...base(110),
        scales: { ...base(110).scales, y: { range: (_u, _min, max) => [0, Math.max(max, 0.5)] } },
        series: [{}, { label: s.soc, stroke: c3, width: 2, points: { show: false } }],
      },
      [x, day.soc],
      socEl,
    ),
  );
  el.appendChild(readout);
  showReadout(null);
}

export function dayTable(day: DaySeries, s: Strings): string {
  const rows = day.hours
    .map(
      (h, i) =>
        `<tr><td>${String(h).padStart(2, "0")}:00</td><td>${fmt(day.price[i], 1)}</td><td>${fmt(day.load[i])}</td>` +
        `<td>${fmt(day.solar[i])}</td><td>${fmt(day.battery[i])}</td><td>${fmt(day.soc[i])}</td></tr>`,
    )
    .join("");
  return (
    `<div class="table-wrap"><table><thead><tr><th>${s.hour}</th><th>${s.price} (EUR/MWh)</th>` +
    `<th>${s.load} (kWh)</th><th>${s.solarSeries} (kWh)</th><th>${s.batterySeries} (kWh)</th>` +
    `<th>${s.soc} (kWh)</th></tr></thead><tbody>${rows}</tbody></table></div>`
  );
}

/** Grouped bars per month: simple battery (neutral) and smart battery (accent). */
export function renderMonthly(el: HTMLElement, rule: number[], optimal: number[], s: Strings): void {
  const W = Math.max(280, el.clientWidth);
  const H = 220;
  const m = { top: 12, right: 8, bottom: 24, left: 44 };
  const iw = W - m.left - m.right;
  const ih = H - m.top - m.bottom;
  const all = [...rule, ...optimal, 0];
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (hi - lo < 1) hi = lo + 1;
  const pad = (hi - lo) * 0.08;
  hi += pad;
  lo = lo < 0 ? lo - pad : 0;
  const y = (v: number) => m.top + ((hi - v) / (hi - lo)) * ih;
  const step = niceStep((hi - lo) / 4);
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);

  const group = iw / 12;
  const bw = Math.min(16, (group - 8) / 2);
  const neutral = css("--neutral-bar");
  const accent = css("--series-3");
  const grid = css("--grid");
  const muted = css("--text-3");

  const bar = (cx: number, v: number, color: string, label: string) => {
    const y0 = y(0);
    const y1 = y(v);
    const top = Math.min(y0, y1);
    const h = Math.max(Math.abs(y1 - y0), 0.5);
    const r = Math.min(4, h, bw / 2);
    // rounded on the data end, square at the baseline
    const d =
      v >= 0
        ? `M${cx},${y0} V${top + r} q0,-${r} ${r},-${r} H${cx + bw - r} q${r},0 ${r},${r} V${y0} Z`
        : `M${cx},${y0} V${top + h - r} q0,${r} ${r},${r} H${cx + bw - r} q${r},0 ${r},-${r} V${y0} Z`;
    return `<path d="${d}" fill="${color}"><title>${label}</title></path>`;
  };

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${s.monthly}">`;
  for (const t of ticks) {
    svg += `<line x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}" stroke="${grid}" stroke-width="1"/>`;
    svg += `<text x="${m.left - 6}" y="${y(t) + 4}" text-anchor="end" font-size="11" fill="${muted}">${t.toFixed(0)}</text>`;
  }
  for (let i = 0; i < 12; i++) {
    const gx = m.left + i * group + (group - (2 * bw + 2)) / 2;
    svg += bar(gx, rule[i], neutral, `${s.month[i]} · ${s.rule}: EUR ${rule[i].toFixed(2)}`);
    svg += bar(gx + bw + 2, optimal[i], accent, `${s.month[i]} · ${s.optimal}: EUR ${optimal[i].toFixed(2)}`);
    svg += `<text x="${m.left + i * group + group / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="${muted}">${s.month[i]}</text>`;
  }
  svg += `<line x1="${m.left}" x2="${W - m.right}" y1="${y(0)}" y2="${y(0)}" stroke="${muted}" stroke-width="1"/>`;
  svg += "</svg>";

  el.innerHTML =
    `<div class="legend"><span><i class="swatch bar" style="background:${neutral}"></i>${s.rule}</span>` +
    `<span><i class="swatch bar" style="background:${accent}"></i>${s.optimal}</span><span class="muted">EUR</span></div>` +
    svg;
}

function niceStep(raw: number): number {
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const f of [1, 2, 2.5, 5, 10]) if (f * p >= raw) return f * p;
  return 10 * p;
}
