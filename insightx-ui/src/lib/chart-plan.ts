// Pick a sensible chart for any query result (no AI): a date and a number
// make a trend, a label and a number make a ranking, one row of numbers
// becomes headline figures.

import { toNumber } from "@/lib/format";
import type { Row } from "@/lib/types";

export type ChartKind = "line" | "area" | "bar" | "pie" | "scatter" | "metric";

export interface ChartPlan {
  kind: ChartKind;
  options: ChartKind[];
  x: string;
  ys: string[];
  xIsDate: boolean;
  horizontal: boolean;
}

const DATE_TEXT = /^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$/;

export function isDateValue(value: unknown): boolean {
  return typeof value === "string" && DATE_TEXT.test(value.trim());
}

function share(rows: Row[], column: string, test: (value: unknown) => boolean): number {
  const values = rows.map((row) => row[column]).filter((value) => value !== null && value !== undefined && value !== "");
  if (!values.length) return 0;
  return values.filter(test).length / values.length;
}

const ID_LIKE = /(^id$|_id$|^id_|_key$|zip|postal|phone|code$)/i;

export function planChart(columns: string[], rows: Row[], preferred?: string): ChartPlan | null {
  if (!rows.length) return null;
  const cols = columns.length ? columns : Object.keys(rows[0] ?? {});
  const sample = rows.slice(0, 200);
  const dates = cols.filter((column) => share(sample, column, isDateValue) >= 0.9);
  const numbers = cols.filter(
    (column) => !dates.includes(column) && share(sample, column, (value) => toNumber(value) !== null) >= 0.9,
  );
  const measures = numbers.filter((column) => !ID_LIKE.test(column));
  const labels = cols.filter((column) => !dates.includes(column) && !numbers.includes(column));

  let plan: ChartPlan | null = null;
  if (rows.length === 1 && measures.length >= 1 && measures.length <= 6 && !dates.length) {
    plan = { kind: "metric", options: ["metric"], x: labels[0] ?? "", ys: measures, xIsDate: false, horizontal: false };
  } else if (dates.length && measures.length) {
    plan = {
      kind: "line",
      options: ["line", "area", "bar"],
      x: dates[0] as string,
      ys: measures.slice(0, 3),
      xIsDate: true,
      horizontal: false,
    };
  } else if ((labels.length || numbers.some((column) => ID_LIKE.test(column))) && measures.length) {
    const x = labels[0] ?? numbers.find((column) => ID_LIKE.test(column)) ?? "";
    const allPositive = rows.every((row) => (toNumber(row[measures[0] as string]) ?? 0) >= 0);
    const longLabels = rows.some((row) => String(row[x] ?? "").length > 12);
    const options: ChartKind[] = ["bar"];
    if (rows.length <= 8 && allPositive && measures.length === 1) options.push("pie");
    plan = {
      kind: "bar",
      options,
      x,
      ys: measures.slice(0, 3),
      xIsDate: false,
      horizontal: longLabels || rows.length > 8,
    };
  } else if (measures.length >= 2) {
    plan = {
      kind: "scatter",
      options: ["scatter"],
      x: measures[0] as string,
      ys: [measures[1] as string],
      xIsDate: false,
      horizontal: false,
    };
  }
  if (plan && preferred && plan.options.includes(preferred as ChartKind)) {
    plan = { ...plan, kind: preferred as ChartKind };
  }
  return plan;
}

/** Recharts wants numbers, not numeric strings; dates sorted oldest first. */
export function chartRows(plan: ChartPlan, rows: Row[], limit = 40): Record<string, string | number | null>[] {
  const out = rows.map((row) => {
    const item: Record<string, string | number | null> = { [plan.x]: row[plan.x] === null ? "(empty)" : String(row[plan.x] ?? "") };
    if (plan.kind === "scatter") item[plan.x] = toNumber(row[plan.x]);
    for (const y of plan.ys) item[y] = toNumber(row[y]);
    return item;
  });
  if (plan.xIsDate) return out.sort((a, b) => String(a[plan.x]).localeCompare(String(b[plan.x])));
  if (plan.kind === "bar" || plan.kind === "pie") return out.slice(0, limit);
  return out;
}
