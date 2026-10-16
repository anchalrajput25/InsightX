"use client";

// Shared Recharts styling: colours from the design tokens, quiet axes,
// and one tooltip look for every chart in the app.

import * as React from "react";
import { formatNumber, humanize } from "@/lib/format";

export const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

export const axisProps = {
  stroke: "var(--chart-axis)",
  tick: { fill: "var(--chart-axis)", fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

export const gridProps = { stroke: "var(--chart-grid)", vertical: false } as const;

export function shortDate(value: unknown): string {
  const text = String(value ?? "");
  const date = new Date(text.length === 7 ? `${text}-01` : text);
  if (Number.isNaN(date.getTime())) return text;
  const hasDay = !/-01([ T]00:00(:00)?)?/.test(text.slice(7)) || text.length > 10;
  return date.toLocaleDateString("en-US", hasDay ? { month: "short", day: "numeric" } : { month: "short", year: "2-digit" });
}

export function shortLabel(value: unknown, length = 14): string {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

interface TooltipEntry {
  name?: string | number;
  value?: number | string | (number | string)[];
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  labelFormatter?: (label: unknown) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="min-w-36 rounded-lg border bg-popover/90 px-3 py-2 text-xs shadow-xl shadow-black/30 backdrop-blur-md">
      {label !== undefined && label !== "" && (
        <p className="mb-1.5 font-medium text-foreground">{labelFormatter ? labelFormatter(label) : String(label)}</p>
      )}
      <div className="space-y-1">
        {payload
          .filter((entry) => entry.value !== null && entry.value !== undefined)
          .map((entry, index) => (
            <div key={`${entry.dataKey ?? index}`} className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <span className="size-2 rounded-[3px]" style={{ background: entry.color }} />
                {humanize(String(entry.name ?? entry.dataKey ?? ""))}
              </span>
              <span className="tabular font-mono text-foreground">
                {Array.isArray(entry.value) ? entry.value.map((v) => formatNumber(v)).join(" – ") : formatNumber(entry.value)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          <span
            className="h-0.5 w-3.5 rounded"
            style={item.dashed ? { borderTop: `2px dashed ${item.color}`, height: 0 } : { background: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}
