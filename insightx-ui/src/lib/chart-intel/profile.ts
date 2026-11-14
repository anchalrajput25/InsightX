// Column profiling: work out what each result column is (a date, a number,
// a category, a place, an id) plus the facts the chart picker needs
// (empty values, distinct values, range, unusual values, unit).

import { toNumber } from "@/lib/format";
import type { Row } from "@/lib/types";

export type ColumnRole = "temporal" | "numeric" | "categorical" | "geo" | "id" | "text";
export type Unit = "currency" | "percent" | "count" | "duration" | "none";
export type DateGrain = "year" | "quarter" | "month" | "day" | "time";

export interface ColumnProfile {
  name: string;
  role: ColumnRole;
  /** Values that are not null / empty. */
  filled: number;
  nulls: number;
  distinct: number;
  unit: Unit;
  /** Percent values stored as 0-1 fractions. */
  fraction: boolean;
  min: number | null;
  max: number | null;
  mean: number | null;
  hasNegative: boolean;
  /** Values outside the 1.5 x IQR fences. */
  outliers: number;
  lowFence: number | null;
  highFence: number | null;
  integer: boolean;
  grain: DateGrain | null;
  geoKind: "country" | "state" | "city" | "region" | "lat" | "lon" | null;
}

const DATE_TEXT = /^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$/;
const QUARTER_TEXT = /^\d{4}[- ]?Q[1-4]$/i;
const ID_NAME = /(^id$|_id$|^id_|_key$|zip|postal|phone|code$|^pk$|uuid|guid)/i;
const YEAR_NAME = /(^|_)(year|yr|fiscal_year)$/i;
const GEO_NAMES: [RegExp, ColumnProfile["geoKind"]][] = [
  [/(^|_)(lat|latitude)$/i, "lat"],
  [/(^|_)(lon|lng|long|longitude)$/i, "lon"],
  [/country|nation/i, "country"],
  [/(^|_)(state|province|county)(_name)?$/i, "state"],
  [/(^|_)city(_name)?$|town/i, "city"],
  [/region|territory|zone|district|area_name/i, "region"],
];
const UNIT_NAMES: [RegExp, Unit][] = [
  [/(pct|percent|percentage|ratio|share|_rate$|^rate$|conversion|margin_pct|churn)/i, "percent"],
  [/(price|revenue|sales|amount|cost|profit|income|spend|salary|wage|payment|paid|fee|budget|gmv|value|total_usd|usd|inr|eur|freight|discount_amt)/i, "currency"],
  [/(duration|seconds|secs|minutes|mins|hours|days_|_days$|latency|elapsed)/i, "duration"],
  [/(count|qty|quantity|num_|^n_|orders|items|units|customers|users|visits|sessions|clicks|stock)/i, "count"],
];

export function isDateValue(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const text = value.trim();
  return DATE_TEXT.test(text) || QUARTER_TEXT.test(text);
}

function present(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "" && !(typeof value === "number" && Number.isNaN(value));
}

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sorted[base + 1];
  return next !== undefined ? (sorted[base] as number) + rest * (next - (sorted[base] as number)) : (sorted[base] as number);
}

function dateGrain(values: string[]): DateGrain {
  const sample = values.slice(0, 200);
  if (sample.some((v) => QUARTER_TEXT.test(v))) return "quarter";
  if (sample.every((v) => /^\d{4}-\d{2}$/.test(v))) return "month";
  if (sample.some((v) => /\d{2}:\d{2}/.test(v) && !/00:00(:00)?/.test(v))) return "time";
  const days = sample.map((v) => v.slice(0, 10));
  if (days.every((v) => v.endsWith("-01")) && new Set(days).size > 1) return "month";
  return "day";
}

export function profileColumn(name: string, rows: Row[]): ColumnProfile {
  const raw = rows.map((row) => row[name]);
  const values = raw.filter(present);
  const filled = values.length;
  const nulls = raw.length - filled;
  const distinct = new Set(values.map((v) => String(v))).size;
  const base: ColumnProfile = {
    name,
    role: "text",
    filled,
    nulls,
    distinct,
    unit: "none",
    fraction: false,
    min: null,
    max: null,
    mean: null,
    hasNegative: false,
    outliers: 0,
    lowFence: null,
    highFence: null,
    integer: false,
    grain: null,
    geoKind: null,
  };
  if (!filled) return base;

  const geo = GEO_NAMES.find(([pattern]) => pattern.test(name));
  base.geoKind = geo ? geo[1] : null;

  const dateShare = values.filter(isDateValue).length / filled;
  if (dateShare >= 0.9) {
    return { ...base, role: "temporal", grain: dateGrain(values.map((v) => String(v).trim())) };
  }

  const numbers = values.map(toNumber).filter((n): n is number => n !== null);
  const numericShare = numbers.length / filled;
  if (numericShare >= 0.9) {
    const sorted = [...numbers].sort((a, b) => a - b);
    const min = sorted[0] as number;
    const max = sorted[sorted.length - 1] as number;
    const mean = numbers.reduce((a, b) => a + b, 0) / numbers.length;
    const integer = numbers.every((n) => Number.isInteger(n));
    const q1 = quantile(sorted, 0.25);
    const q3 = quantile(sorted, 0.75);
    const iqr = q3 - q1;
    const lowFence = q1 - 1.5 * iqr;
    const highFence = q3 + 1.5 * iqr;
    const outliers = iqr > 0 ? numbers.filter((n) => n < lowFence || n > highFence).length : 0;
    const unitMatch = UNIT_NAMES.find(([pattern]) => pattern.test(name));
    const unit: Unit = unitMatch ? unitMatch[1] : "none";
    const numeric: ColumnProfile = {
      ...base,
      role: "numeric",
      min,
      max,
      mean,
      integer,
      hasNegative: min < 0,
      outliers,
      lowFence,
      highFence,
      unit,
      fraction: unit === "percent" && max <= 1.0001 && min >= -1.0001,
    };
    if (YEAR_NAME.test(name) && integer && min >= 1900 && max <= 2100) {
      return { ...numeric, role: "temporal", grain: "year", unit: "none" };
    }
    if (base.geoKind === "lat" || base.geoKind === "lon") return { ...numeric, role: "geo" };
    if (ID_NAME.test(name) || (integer && distinct === filled && filled > 20 && /(^|_)(no|num|number)$/i.test(name))) {
      return { ...numeric, role: "id" };
    }
    return numeric;
  }

  if (ID_NAME.test(name) && distinct > 0.9 * filled && filled > 20) return { ...base, role: "id" };
  if (base.geoKind) return { ...base, role: "geo" };
  // Categories repeat or stay few; long unique text is free text.
  const avgLength = values.reduce<number>((sum, v) => sum + String(v).length, 0) / filled;
  if (distinct <= Math.max(60, filled * 0.6) && avgLength <= 60) return { ...base, role: "categorical" };
  return { ...base, role: avgLength > 60 ? "text" : "categorical" };
}

export interface DataProfile {
  rowCount: number;
  columns: ColumnProfile[];
  temporal: ColumnProfile[];
  measures: ColumnProfile[];
  /** Categorical and place-name columns, best (fewest distinct) first. */
  dimensions: ColumnProfile[];
  ids: ColumnProfile[];
}

export function profileData(columns: string[], rows: Row[]): DataProfile {
  const sample = rows.slice(0, 2000);
  const names = columns.length ? columns : Object.keys(rows[0] ?? {});
  const profiles = names.map((name) => profileColumn(name, sample));
  return {
    rowCount: rows.length,
    columns: profiles,
    temporal: profiles.filter((p) => p.role === "temporal"),
    measures: profiles.filter((p) => p.role === "numeric"),
    dimensions: profiles
      .filter((p) => p.role === "categorical" || (p.role === "geo" && p.geoKind !== "lat" && p.geoKind !== "lon"))
      .sort((a, b) => a.distinct - b.distinct),
    ids: profiles.filter((p) => p.role === "id"),
  };
}
