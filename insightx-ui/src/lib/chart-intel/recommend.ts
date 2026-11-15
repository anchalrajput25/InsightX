// Chart intelligence: pick the right chart for the data and the question,
// list the other charts that are also honest for it, and refuse the ones
// that would mislead (a donut with negative parts, a line over unordered
// labels, a pie with 40 slices).

import type { ColumnProfile, DataProfile } from "@/lib/chart-intel/profile";
import { profileData } from "@/lib/chart-intel/profile";
import type { Row } from "@/lib/types";

export type SmartKind =
  | "line"
  | "bar"
  | "stacked-bar"
  | "area"
  | "stacked-area"
  | "donut"
  | "scatter"
  | "bubble"
  | "combo"
  | "histogram"
  | "metric";

export const KIND_LABELS: Record<SmartKind, string> = {
  line: "Line",
  bar: "Bar",
  "stacked-bar": "Stacked bar",
  area: "Area",
  "stacked-area": "Stacked area",
  donut: "Donut",
  scatter: "Scatter",
  bubble: "Bubble",
  combo: "Bar + line",
  histogram: "Histogram",
  metric: "Numbers",
};

export type Intent = "trend" | "compare" | "share" | "correlation" | "distribution" | "composition" | "none";
export type Aggregation = "none" | "sum" | "avg";

export interface SmartPlan {
  kind: SmartKind;
  /** Every chart type that is honest for this data, best first. */
  options: SmartKind[];
  /** X axis / category column ("" for histogram and metric). */
  x: string;
  xRole: "temporal" | "category" | "numeric" | "none";
  /** Second category used to split bars or areas into series. */
  split: string | null;
  /** Measures (numbers) drawn on the value axis. */
  ys: string[];
  /** Bubble size measure. */
  size: string | null;
  /** Row label for scatter / bubble points. */
  label: string | null;
  aggregation: Aggregation;
  horizontal: boolean;
  intent: Intent;
  /** One-line reason the chart was picked, shown on hover. */
  reason: string;
  profile: DataProfile;
}

const INTENT_WORDS: [Intent, RegExp][] = [
  ["distribution", /\b(distribution|histogram|spread|frequency|how (are|is) .* distributed|range of)\b/i],
  ["correlation", /\b(correlat\w*|relationship|relate[sd]?|versus|vs\.?|against|impact of|affect)\b/i],
  ["composition", /\b(composition|mix|stack(ed)?|make ?up|split by|broken down)\b/i],
  ["share", /\b(share|proportion|percentage of|percent of|breakdown|portion|contribut\w*)\b/i],
  ["trend", /\b(trend|over time|growth|monthly|weekly|daily|yearly|per (day|week|month|year)|by (day|week|month|quarter|year)|timeline|history|since)\b/i],
  ["compare", /\b(compare|comparison|top|bottom|rank\w*|highest|lowest|most|least|best|worst|by)\b/i],
];

export function detectIntent(question?: string): Intent {
  if (!question) return "none";
  for (const [intent, pattern] of INTENT_WORDS) if (pattern.test(question)) return intent;
  return "none";
}

/** The backend sends "pie", "top" etc.; map to our kinds. */
function preferredKind(preferred?: string): SmartKind | null {
  if (!preferred) return null;
  const map: Record<string, SmartKind> = {
    pie: "donut",
    donut: "donut",
    line: "line",
    area: "area",
    bar: "bar",
    scatter: "scatter",
    metric: "metric",
    histogram: "histogram",
    bubble: "bubble",
    combo: "combo",
    "stacked-bar": "stacked-bar",
    "stacked-area": "stacked-area",
  };
  return map[preferred] ?? null;
}

function hasDuplicates(rows: Row[], keys: string[]): boolean {
  const seen = new Set<string>();
  for (const row of rows) {
    const key = keys.map((k) => String(row[k] ?? "")).join("\u0001");
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}

function aggregationFor(measure: ColumnProfile | undefined): Aggregation {
  // Rates and averages must not be added up; totals and counts can be.
  if (!measure) return "sum";
  if (measure.unit === "percent" || /avg|average|mean|median|rate|ratio|score|rating|price/i.test(measure.name)) return "avg";
  return "sum";
}

/** True when two measures differ in size so much that one axis hides the other. */
function differentScales(a: ColumnProfile, b: ColumnProfile): boolean {
  const sa = Math.max(Math.abs(a.max ?? 0), Math.abs(a.min ?? 0));
  const sb = Math.max(Math.abs(b.max ?? 0), Math.abs(b.min ?? 0));
  if (!sa || !sb) return false;
  return Math.max(sa, sb) / Math.min(sa, sb) >= 15 || a.unit !== b.unit;
}

function sameUnitPositive(measures: ColumnProfile[]): boolean {
  return measures.length > 1 && measures.every((m) => !m.hasNegative && m.unit === measures[0]?.unit);
}

function unique<T>(items: T[]): T[] {
  return [...new Set(items)];
}

export function recommendChart(
  columns: string[],
  rows: Row[],
  options: { preferred?: string; question?: string } = {},
): SmartPlan | null {
  if (!rows.length) return null;
  const profile = profileData(columns, rows);
  const intent = detectIntent(options.question);
  const measures = profile.measures;
  const dims = profile.dimensions.length ? profile.dimensions : profile.ids;
  const time = profile.temporal[0];
  const n = rows.length;

  const blank = {
    x: "",
    xRole: "none" as const,
    split: null,
    ys: [] as string[],
    size: null,
    label: null,
    aggregation: "none" as Aggregation,
    horizontal: false,
    intent,
    profile,
  };
  let plan: SmartPlan | null = null;

  if (n === 1 && measures.length >= 1 && measures.length <= 6 && !time) {
    plan = { ...blank, kind: "metric", options: ["metric"], ys: measures.map((m) => m.name), reason: "One row of numbers reads best as headline figures." };
  } else if (time && measures.length) {
    // Over time
    const split = dims.find((d) => d.distinct >= 2 && d.distinct <= 10) ?? null;
    const ys = measures.slice(0, split ? 1 : 3);
    const keys = split ? [time.name, split.name] : [time.name];
    const aggregation = hasDuplicates(rows, keys) ? aggregationFor(ys[0]) : "none";
    const kinds: SmartKind[] = ["line", "area", "bar"];
    if (split) kinds.push("stacked-area", "stacked-bar");
    else if (ys.length >= 2) {
      if (sameUnitPositive(ys)) kinds.push("stacked-area", "stacked-bar");
      kinds.push("combo");
    }
    let kind: SmartKind = "line";
    if (ys.length === 2 && differentScales(ys[0] as ColumnProfile, ys[1] as ColumnProfile)) kind = "combo";
    if (intent === "composition" && kinds.includes("stacked-area")) kind = "stacked-area";
    if (time.distinct <= 6 && !split && ys.length === 1 && time.grain !== "time") kind = intent === "trend" ? "line" : "bar";
    plan = {
      ...blank,
      kind,
      options: unique([kind, ...kinds]),
      x: time.name,
      xRole: "temporal",
      split: split?.name ?? null,
      ys: ys.map((m) => m.name),
      aggregation,
      reason:
        kind === "combo"
          ? `${ys[0]?.name} and ${ys[1]?.name} have very different sizes, so they get their own axes.`
          : split
            ? `A date, a number and a small group column: one line per ${split.name}.`
            : "A date and a number make a trend over time.",
    };
  } else if (dims.length && measures.length) {
    // By category
    // The column the query listed first is the main category; a second
    // category with few values splits the bars into stacks.
    const ordered = [...dims].sort((a, b) => profile.columns.indexOf(a) - profile.columns.indexOf(b));
    const x = ordered[0] as ColumnProfile;
    const split = ordered.slice(1).find((d) => d.distinct >= 2 && d.distinct <= 12) ?? null;
    const ys = measures.slice(0, split ? 1 : 3);
    const keys = split ? [x.name, split.name] : [x.name];
    const aggregation = hasDuplicates(rows, keys) ? aggregationFor(ys[0]) : "none";
    const groups = x.distinct;
    const kinds: SmartKind[] = [];
    let kind: SmartKind;
    if (split) {
      kinds.push("stacked-bar", "bar");
      kind = "stacked-bar";
    } else if (ys.length === 1) {
      const m = ys[0] as ColumnProfile;
      kinds.push("bar");
      const partsOfWhole = !m.hasNegative && aggregationFor(m) === "sum" && groups >= 2 && groups <= 8;
      if (partsOfWhole) kinds.push("donut");
      kind = intent === "share" && partsOfWhole ? "donut" : "bar";
    } else {
      kinds.push("bar");
      if (sameUnitPositive(ys)) kinds.push("stacked-bar");
      kinds.push("combo");
      if (n >= 5) kinds.push("scatter");
      if (ys.length >= 3 && n >= 5) kinds.push("bubble");
      // Numbers of very different sizes on one axis hide the small ones: use two axes.
      kind = differentScales(ys[0] as ColumnProfile, ys[1] as ColumnProfile) ? "combo" : "bar";
      if (intent === "correlation" && n >= 5) kind = ys.length >= 3 ? "bubble" : "scatter";
      if (intent === "composition" && kinds.includes("stacked-bar")) kind = "stacked-bar";
    }
    if (measures.length && n >= 20) kinds.push("histogram");
    const longLabels = rows.some((row) => String(row[x.name] ?? "").length > 12);
    plan = {
      ...blank,
      kind,
      options: unique([kind, ...kinds]),
      x: x.name,
      xRole: "category",
      split: split?.name ?? null,
      ys: ys.map((m) => m.name),
      size: ys.length >= 3 ? (ys[2]?.name ?? null) : null,
      label: x.name,
      aggregation,
      horizontal: longLabels || groups > 8,
      reason:
        kind === "donut"
          ? "A few parts of one total: a donut shows each share."
          : split
            ? `Two group columns: bars by ${x.name}, stacked by ${split.name}.`
            : kind === "scatter" || kind === "bubble"
              ? "The question asks how numbers relate, so each row becomes a point."
              : "A group column and a number: bars compare the groups.",
    };
  } else if (measures.length >= 2) {
    // Numbers only
    const [a, b, c] = measures;
    const kinds: SmartKind[] = ["scatter"];
    if (c) kinds.push("bubble");
    if (n >= 10) kinds.push("histogram");
    let kind: SmartKind = intent === "distribution" && n >= 10 ? "histogram" : "scatter";
    if (c && intent !== "distribution") kind = "bubble";
    plan = {
      ...blank,
      kind,
      options: unique([kind, ...kinds]),
      x: (a as ColumnProfile).name,
      xRole: "numeric",
      ys: kind === "histogram" ? [(a as ColumnProfile).name] : [(b as ColumnProfile).name],
      size: c?.name ?? null,
      label: profile.ids[0]?.name ?? null,
      reason: kind === "histogram" ? "The question asks how values are spread." : "Two numbers per row: a scatter shows how they move together.",
    };
  } else if (measures.length === 1 && n >= 5) {
    const m = measures[0] as ColumnProfile;
    plan = { ...blank, kind: "histogram", options: ["histogram"], ys: [m.name], reason: "One number over many rows: a histogram shows how the values are spread." };
  }

  if (!plan) return null;
  const wanted = preferredKind(options.preferred);
  if (wanted && plan.options.includes(wanted) && intent === "none") plan = { ...plan, kind: wanted };
  return plan;
}

/** Re-target a plan to another chart type the user picked. */
export function withKind(plan: SmartPlan, kind: SmartKind): SmartPlan {
  return plan.options.includes(kind) ? { ...plan, kind } : plan;
}

export interface Validation {
  ok: boolean;
  problems: string[];
}

/** Check a chart before drawing it so a broken or misleading chart never shows. */
export function validatePlan(plan: SmartPlan, rows: Row[]): Validation {
  const problems: string[] = [];
  const names = new Set(plan.profile.columns.map((c) => c.name));
  const need = [plan.x, ...plan.ys, plan.split ?? "", plan.size ?? ""].filter(Boolean);
  for (const column of need) if (!names.has(column)) problems.push(`Column "${column}" is missing.`);
  const byName = new Map(plan.profile.columns.map((c) => [c.name, c]));
  for (const y of plan.ys) {
    const p = byName.get(y);
    if (p && p.role !== "numeric" && p.role !== "id") problems.push(`"${y}" is not a number.`);
  }
  if (plan.kind === "donut") {
    const y = byName.get(plan.ys[0] ?? "");
    if (y?.hasNegative) problems.push("A donut cannot show negative parts.");
  }
  if ((plan.kind === "line" || plan.kind === "area" || plan.kind === "stacked-area") && plan.xRole !== "temporal") {
    problems.push("Lines need an ordered axis such as dates.");
  }
  if (plan.kind === "histogram" && rows.length < 5) problems.push("Too few values for a histogram.");
  if ((plan.kind === "scatter" || plan.kind === "bubble") && rows.length < 3) problems.push("Too few points to compare.");
  if (plan.kind === "combo" && plan.ys.length < 2) problems.push("Bar + line needs two numbers.");
  if (plan.kind === "bubble" && !plan.size && plan.ys.length < 3) problems.push("Bubbles need a third number for size.");
  if (plan.kind !== "metric" && plan.kind !== "histogram" && !plan.x) problems.push("No column for the axis.");
  return { ok: problems.length === 0, problems };
}
