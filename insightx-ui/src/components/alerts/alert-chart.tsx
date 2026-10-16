"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { axisProps, gridProps, Legend, shortDate } from "@/components/charts/chart-kit";
import { formatNumber } from "@/lib/format";
import type { AlertRule, Backtest } from "@/lib/types";

interface Point {
  t: string;
  v: number;
  expected: number | null;
  band: [number, number] | null;
  fired: number | null;
  severity: string | null;
}

function TooltipBody({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-xl">
      <p className="mb-1 font-medium">{shortDate(point.t)}</p>
      <p className="tabular">Value: {formatNumber(point.v)}</p>
      {point.expected !== null && <p className="tabular text-muted-foreground">Expected: {formatNumber(point.expected)}</p>}
      {point.band && (
        <p className="tabular text-muted-foreground">
          Normal: {formatNumber(point.band[0])} to {formatNumber(point.band[1])}
        </p>
      )}
      {point.fired !== null && <p className={point.severity === "critical" ? "text-critical" : "text-warning"}>Would have alerted ({point.severity})</p>}
    </div>
  );
}

export function AlertChart({ backtest, rule, height = 220 }: { backtest: Backtest; rule: AlertRule; height?: number }) {
  const data: Point[] = backtest.series
    .filter((point) => point.v !== null && point.v !== undefined)
    .map((point) => ({
      t: point.t,
      v: point.v,
      expected: point.expected,
      band: point.low !== null && point.high !== null ? [point.low, point.high] : null,
      fired: point.fired ? point.v : null,
      severity: point.severity,
    }));
  if (data.length < 2) {
    return <p className="flex h-24 items-center justify-center rounded-lg border border-dashed text-xs text-muted-foreground">Not enough history to draw yet.</p>;
  }
  const hasBand = rule.type === "smart" && data.some((point) => point.band);
  const legend = [
    { label: "Value", color: "var(--foreground)" },
    ...(hasBand ? [{ label: "Normal range", color: "var(--chart-1)" }, { label: "Expected", color: "var(--chart-1)", dashed: true }] : []),
    ...(rule.type === "limit" ? [{ label: "Limit", color: "var(--critical)", dashed: true }] : []),
    ...(data.some((point) => point.fired !== null) ? [{ label: "Would have alerted", color: "var(--warning)" }] : []),
  ];
  return (
    <div className="space-y-2">
      <Legend items={legend} />
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis {...axisProps} dataKey="t" tickFormatter={shortDate} minTickGap={28} />
            <YAxis {...axisProps} width={48} tickFormatter={(v) => formatNumber(v)} domain={["auto", "auto"]} />
            <Tooltip content={<TooltipBody />} />
            {hasBand && <Area type="monotone" dataKey="band" stroke="none" fill="var(--chart-1)" fillOpacity={0.14} isAnimationActive={false} connectNulls />}
            {hasBand && <Line type="monotone" dataKey="expected" stroke="var(--chart-1)" strokeWidth={1.2} strokeDasharray="4 4" dot={false} isAnimationActive={false} connectNulls />}
            {rule.type === "limit" && <ReferenceLine y={rule.value ?? 0} stroke="var(--critical)" strokeDasharray="6 4" strokeWidth={1.5} />}
            <Line type="monotone" dataKey="v" stroke="var(--foreground)" strokeOpacity={0.85} strokeWidth={1.7} dot={false} isAnimationActive={false} />
            <Scatter dataKey="fired" fill="var(--warning)" stroke="var(--card)" strokeWidth={1.5} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
