// Turn raw query rows into the exact numbers a chart draws: drop empty
// values, group and add up (or average), sort, keep the top groups, split
// into series, bin a histogram. Every step that changes the data leaves a
// plain-English note so the chart never hides what it did.

import { toNumber } from "@/lib/format";
import type { ColumnProfile } from "@/lib/chart-intel/profile";
import type { SmartPlan } from "@/lib/chart-intel/recommend";
import type { Row } from "@/lib/types";

export type SortMode = "natural" | "value-desc" | "value-asc" | "label";

export interface ViewState {
  sort: SortMode;
  /** Keep only the biggest N groups (null = automatic). */
  topN: number | null;
  logScale: boolean;
}

export const DEFAULT_VIEW: ViewState = { sort: "natural", topN: null, logScale: false };

export interface SeriesData {
  name: string;
  /** Measure column the series comes from. */
  measure: string;
  values: (number | null)[];
}

export interface CategoryData {
  type: "category";
  categories: string[];
  series: SeriesData[];
}

export interface PointData {
  type: "points";
  xName: string;
  yName: string;
  sizeName: string | null;
  points: { x: number; y: number; size: number | null; label: string; unusual: boolean }[];
  /** Least-squares line through the points. */
  trend: { slope: number; intercept: number } | null;
  correlation: number | null;
}

export interface HistogramData {
  type: "histogram";
  measure: string;
  bins: { from: number; to: number; count: number }[];
  median: number;
  outliers: number;
}

export interface MetricData {
  type: "metric";
  values: { name: string; value: number | null }[];
}

export type Prepared = (CategoryData | PointData | HistogramData | MetricData) & {
  notes: string[];
  /** Rows behind what is drawn (after filters and grouping) for the data view and CSV. */
  table: { columns: string[]; rows: Row[] };
};

const OTHER = "Other";

function label(value: unknown): string {
  if (value === null || value === undefined || value === "") return "(empty)";
  return String(value);
}

function profileOf(plan: SmartPlan, name: string): ColumnProfile | undefined {
  return plan.profile.columns.find((c) => c.name === name);
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function compareLabels(a: string, b: string, temporal: boolean): number {
  if (temporal) {
    const na = Number(a);
    const nb = Number(b);
    if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
    return a.localeCompare(b);
  }
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function prepareCategories(plan: SmartPlan, rows: Row[], view: ViewState): Prepared {
  const notes: string[] = [];
  const temporal = plan.xRole === "temporal";
  const avg = plan.aggregation === "avg";
  const measures = plan.kind === "combo" ? plan.ys.slice(0, 2) : plan.ys;

  // 1. drop rows with no x or no numbers
  let emptyX = 0;
  let emptyY = 0;
  const usable = rows.filter((row) => {
    const x = row[plan.x];
    if (x === null || x === undefined || x === "") {
      emptyX += 1;
      return false;
    }
    if (measures.every((m) => toNumber(row[m]) === null)) {
      emptyY += 1;
      return false;
    }
    return true;
  });
  if (emptyX) notes.push(`${plural(emptyX, "row")} with no ${plan.x} left out.`);
  if (emptyY) notes.push(`${plural(emptyY, "row")} with no numbers left out.`);

  // 2. group (x, split) -> sum / count per measure
  const order: string[] = [];
  const splitOrder: string[] = [];
  const seen = new Set<string>();
  const cells = new Map<string, { sum: number; count: number }>();
  for (const row of usable) {
    const x = label(row[plan.x]);
    if (!seen.has(x)) {
      seen.add(x);
      order.push(x);
    }
    const s = plan.split ? label(row[plan.split]) : null;
    if (s !== null && !splitOrder.includes(s)) splitOrder.push(s);
    for (const m of measures) {
      const value = toNumber(row[m]);
      if (value === null) continue;
      const key = `${x}\u0001${s ?? m}`;
      const cell = cells.get(key) ?? { sum: 0, count: 0 };
      cell.sum += value;
      cell.count += 1;
      cells.set(key, cell);
    }
  }
  if (plan.aggregation !== "none") {
    notes.push(`Rows with the same ${plan.split ? `${plan.x} and ${plan.split}` : plan.x} were ${avg ? "averaged" : "added up"}.`);
  }
  const valueOf = (x: string, key: string): number | null => {
    const cell = cells.get(`${x}\u0001${key}`);
    if (!cell || !cell.count) return null;
    return avg ? cell.sum / cell.count : cell.sum;
  };
  const seriesKeys = plan.split ? splitOrder : measures;
  const totalOf = (x: string) => seriesKeys.reduce((sum, key) => sum + (valueOf(x, key) ?? 0), 0);

  // 3. order
  let categories = [...order];
  if (temporal) categories.sort((a, b) => compareLabels(a, b, true));
  else if (view.sort === "value-desc") categories.sort((a, b) => totalOf(b) - totalOf(a));
  else if (view.sort === "value-asc") categories.sort((a, b) => totalOf(a) - totalOf(b));
  else if (view.sort === "label") categories.sort((a, b) => compareLabels(a, b, false));

  // 4. keep the biggest groups; the rest become "Other" when adding up is honest
  let otherValues: Map<string, number> | null = null;
  if (!temporal) {
    const auto = plan.kind === "donut" ? 8 : 25;
    const limit = view.topN ?? (categories.length > auto ? (plan.kind === "donut" ? 7 : 20) : null);
    if (limit && categories.length > limit) {
      const ranked = [...categories].sort((a, b) => totalOf(b) - totalOf(a));
      const keep = new Set(ranked.slice(0, limit));
      const dropped = categories.filter((c) => !keep.has(c));
      categories = categories.filter((c) => keep.has(c));
      if (!avg && plan.kind !== "scatter") {
        otherValues = new Map(seriesKeys.map((key) => [key, dropped.reduce((sum, x) => sum + (valueOf(x, key) ?? 0), 0)]));
        notes.push(`Showing the top ${limit} of ${order.length} ${plan.x} values; the other ${dropped.length} are grouped as "${OTHER}".`);
      } else {
        notes.push(`Showing the top ${limit} of ${order.length} ${plan.x} values.`);
      }
    }
  }

  // 5. too many split values: keep the biggest 10
  let keys = seriesKeys;
  if (plan.split && keys.length > 10) {
    const totals = new Map(keys.map((key) => [key, categories.reduce((sum, x) => sum + (valueOf(x, key) ?? 0), 0)]));
    keys = [...keys].sort((a, b) => (totals.get(b) ?? 0) - (totals.get(a) ?? 0)).slice(0, 10);
    notes.push(`Showing the 10 biggest ${plan.split} values out of ${seriesKeys.length}.`);
  }

  const series: SeriesData[] = keys.map((key) => {
    const values = categories.map((x) => valueOf(x, key));
    if (otherValues) values.push(otherValues.get(key) ?? 0);
    return { name: key, measure: plan.split ? (measures[0] as string) : key, values };
  });
  if (otherValues) categories = [...categories, OTHER];

  // table: one row per drawn group
  const tableColumns = [plan.x, ...keys];
  const tableRows: Row[] = categories.map((x, i) => {
    const row: Row = { [plan.x]: x };
    series.forEach((s) => {
      row[s.name] = s.values[i] ?? null;
    });
    return row;
  });
  return { type: "category", categories, series, notes, table: { columns: tableColumns, rows: tableRows } };
}

function pearson(xs: number[], ys: number[]): number | null {
  const n = xs.length;
  if (n < 3) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = (xs[i] as number) - mx;
    const dy = (ys[i] as number) - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  if (!sxx || !syy) return null;
  return sxy / Math.sqrt(sxx * syy);
}

function preparePoints(plan: SmartPlan, rows: Row[]): Prepared {
  const notes: string[] = [];
  const [xName, yName, sizeName] =
    plan.xRole === "numeric"
      ? [plan.x, plan.ys[0] as string, plan.kind === "bubble" ? plan.size : null]
      : [plan.ys[0] as string, plan.ys[1] as string, plan.kind === "bubble" ? (plan.ys[2] ?? plan.size) : null];
  const xp = profileOf(plan, xName);
  const yp = profileOf(plan, yName);
  let skipped = 0;
  const points: PointData["points"] = [];
  rows.forEach((row, index) => {
    const x = toNumber(row[xName]);
    const y = toNumber(row[yName]);
    const size = sizeName ? toNumber(row[sizeName]) : null;
    if (x === null || y === null || (sizeName && size === null)) {
      skipped += 1;
      return;
    }
    const outside = (p: ColumnProfile | undefined, v: number) =>
      !!p && p.lowFence !== null && p.highFence !== null && p.lowFence !== p.highFence && (v < p.lowFence || v > p.highFence);
    points.push({
      x,
      y,
      size,
      label: plan.label ? label(row[plan.label]) : `Row ${index + 1}`,
      unusual: outside(xp, x) || outside(yp, y),
    });
  });
  if (skipped) notes.push(`${plural(skipped, "row")} with missing numbers left out.`);
  const unusual = points.filter((p) => p.unusual).length;
  if (unusual) notes.push(`${plural(unusual, "unusual point")} (far from the rest) ${unusual === 1 ? "is" : "are"} marked.`);
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const r = pearson(xs, ys);
  let trend: PointData["trend"] = null;
  if (r !== null && points.length >= 3) {
    const mx = xs.reduce((a, b) => a + b, 0) / xs.length;
    const my = ys.reduce((a, b) => a + b, 0) / ys.length;
    let num = 0;
    let den = 0;
    xs.forEach((x, i) => {
      num += (x - mx) * ((ys[i] as number) - my);
      den += (x - mx) ** 2;
    });
    if (den) trend = { slope: num / den, intercept: my - (num / den) * mx };
  }
  const columns = [plan.label ?? "point", xName, yName, ...(sizeName ? [sizeName] : [])].filter((c, i, all) => all.indexOf(c) === i);
  const tableRows: Row[] = points.map((p) => {
    const row: Row = { [plan.label ?? "point"]: p.label, [xName]: p.x, [yName]: p.y };
    if (sizeName) row[sizeName] = p.size;
    return row;
  });
  return { type: "points", xName, yName, sizeName, points, trend, correlation: r, notes, table: { columns, rows: tableRows } };
}

function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return nice * power;
}

function prepareHistogram(plan: SmartPlan, rows: Row[]): Prepared {
  const measure = (plan.xRole === "numeric" && plan.kind === "histogram" ? plan.x : plan.ys[0]) as string;
  const values = rows.map((row) => toNumber(row[measure])).filter((v): v is number => v !== null);
  const notes: string[] = [];
  const empty = rows.length - values.length;
  if (empty) notes.push(`${plural(empty, "empty value")} left out.`);
  const sorted = [...values].sort((a, b) => a - b);
  const min = sorted[0] ?? 0;
  const max = sorted[sorted.length - 1] ?? 0;
  const q = (p: number) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * p)))] ?? 0;
  const iqr = q(0.75) - q(0.25);
  // Freedman-Diaconis width, kept between 5 and 40 bars
  let width = iqr > 0 ? (2 * iqr) / Math.cbrt(values.length) : (max - min) / Math.max(5, Math.ceil(Math.log2(values.length) + 1));
  if (!width || !Number.isFinite(width)) width = 1;
  let count = Math.ceil((max - min) / width) || 1;
  if (count > 40) width = (max - min) / 40;
  if (count < 5 && max > min) width = (max - min) / 5;
  width = niceStep(width);
  const start = Math.floor(min / width) * width;
  count = Math.max(1, Math.ceil((max - start) / width + 1e-9));
  if (start + count * width <= max) count += 1;
  const bins = Array.from({ length: count }, (_, i) => ({ from: start + i * width, to: start + (i + 1) * width, count: 0 }));
  for (const v of values) {
    const index = Math.min(bins.length - 1, Math.floor((v - start) / width));
    const bin = bins[index];
    if (bin) bin.count += 1;
  }
  const profile = profileOf(plan, measure);
  const outliers = profile?.outliers ?? 0;
  if (outliers) notes.push(`${plural(outliers, "unusual value")} sit far from the rest.`);
  const columns = ["from", "to", "count"];
  return {
    type: "histogram",
    measure,
    bins,
    median: q(0.5),
    outliers,
    notes,
    table: { columns, rows: bins.map((b) => ({ from: b.from, to: b.to, count: b.count })) },
  };
}

export function prepareData(plan: SmartPlan, rows: Row[], view: ViewState = DEFAULT_VIEW): Prepared {
  if (plan.kind === "metric") {
    const row = rows[0] ?? {};
    const values = plan.ys.map((name) => ({ name, value: toNumber(row[name]) }));
    return { type: "metric", values, notes: [], table: { columns: plan.ys, rows: [row] } };
  }
  if (plan.kind === "histogram") return prepareHistogram(plan, rows);
  if (plan.kind === "scatter" || plan.kind === "bubble") return preparePoints(plan, rows);
  return prepareCategories(plan, rows, view);
}
