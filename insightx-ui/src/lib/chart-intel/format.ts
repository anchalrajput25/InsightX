// Value labels with the right unit: money stays compact, rates show as
// percents (0.25 -> 25%), counts stay whole.

import { formatNumber, humanize } from "@/lib/format";
import type { ColumnProfile, Unit } from "@/lib/chart-intel/profile";

export interface ValueFormat {
  unit: Unit;
  fraction: boolean;
}

export function formatOf(profile: ColumnProfile | undefined): ValueFormat {
  return { unit: profile?.unit ?? "none", fraction: profile?.fraction ?? false };
}

export function formatValue(value: unknown, format: ValueFormat, compactFrom = 10_000): string {
  if (value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value))) return "-";
  const number = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(number)) return String(value);
  if (format.unit === "percent") {
    const pct = format.fraction ? number * 100 : number;
    return `${Math.abs(pct) >= 100 ? Math.round(pct).toLocaleString("en-US") : pct.toFixed(1)}%`;
  }
  return formatNumber(number, { compactFrom });
}

export function axisLabel(value: number, format: ValueFormat): string {
  return formatValue(value, format, 1000);
}

/** "total_revenue" + currency -> "Total revenue" ; adds "(%)" for rates. */
export function measureTitle(name: string, format?: ValueFormat): string {
  const base = humanize(name);
  if (format?.unit === "percent" && !/%|percent/i.test(base)) return `${base} (%)`;
  return base;
}
