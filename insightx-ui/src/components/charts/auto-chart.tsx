"use client";

import * as React from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AreaChart as AreaIcon, BarChart3, LineChart as LineIcon, PieChart as PieIcon } from "lucide-react";
import { chartRows, planChart, type ChartKind, type ChartPlan } from "@/lib/chart-plan";
import { formatNumber, humanize } from "@/lib/format";
import type { Row } from "@/lib/types";
import { cn } from "@/lib/utils";
import { axisProps, ChartTooltip, gridProps, Legend, SERIES, shortDate, shortLabel } from "@/components/charts/chart-kit";

const KIND_ICONS: Partial<Record<ChartKind, typeof BarChart3>> = {
  bar: BarChart3,
  line: LineIcon,
  area: AreaIcon,
  pie: PieIcon,
};

function KindSwitch({ plan, onChange }: { plan: ChartPlan; onChange: (kind: ChartKind) => void }) {
  if (plan.options.length < 2) return null;
  return (
    <div role="radiogroup" aria-label="Chart type" className="inline-flex rounded-md border p-0.5">
      {plan.options.map((kind) => {
        const Icon = KIND_ICONS[kind] ?? BarChart3;
        const active = plan.kind === kind;
        return (
          <button
            key={kind}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={`${humanize(kind)} chart`}
            onClick={() => onChange(kind)}
            className={cn(
              "flex size-6 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground",
              active && "bg-accent text-foreground",
            )}
          >
            <Icon className="size-3.5" />
          </button>
        );
      })}
    </div>
  );
}

function Metrics({ plan, row }: { plan: ChartPlan; row: Row }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {plan.ys.map((column) => (
        <div key={column} className="rounded-lg border bg-surface px-4 py-3">
          <p className="text-xs text-muted-foreground">{humanize(column)}</p>
          <p className="tabular mt-1 text-2xl font-semibold tracking-tight">{formatNumber(row[column], { compactFrom: 1e7 })}</p>
        </div>
      ))}
    </div>
  );
}

export function AutoChart({
  columns,
  rows,
  preferred,
  height = 260,
  highlight,
  className,
  showSwitch = true,
}: {
  columns: string[];
  rows: Row[];
  preferred?: string;
  height?: number;
  /** A category value to draw in the accent colour (the answer's pick). */
  highlight?: string;
  className?: string;
  showSwitch?: boolean;
}) {
  const initial = React.useMemo(() => planChart(columns, rows, preferred), [columns, rows, preferred]);
  const [kind, setKind] = React.useState<ChartKind | null>(null);
  if (!initial) return null;
  const plan: ChartPlan = kind && initial.options.includes(kind) ? { ...initial, kind } : initial;

  if (plan.kind === "metric") return <Metrics plan={plan} row={rows[0] as Row} />;

  const data = chartRows(plan, rows);
  const xFormatter = (value: unknown) => (plan.xIsDate ? shortDate(value) : shortLabel(value));
  const series = plan.ys.map((key, index) => ({ key, color: SERIES[index % SERIES.length] as string }));

  let chart: React.ReactElement;
  if (plan.kind === "pie") {
    const key = plan.ys[0] as string;
    chart = (
      <PieChart>
        <Tooltip content={<ChartTooltip />} />
        <Pie data={data} dataKey={key} nameKey={plan.x} innerRadius="58%" outerRadius="88%" paddingAngle={2} stroke="var(--card)" strokeWidth={2}>
          {data.map((entry, index) => (
            <Cell key={String(entry[plan.x])} fill={SERIES[index % SERIES.length]} />
          ))}
        </Pie>
      </PieChart>
    );
  } else if (plan.kind === "scatter") {
    chart = (
      <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid {...gridProps} vertical />
        <XAxis {...axisProps} dataKey={plan.x} type="number" name={humanize(plan.x)} tickFormatter={(v) => formatNumber(v)} />
        <YAxis {...axisProps} dataKey={plan.ys[0]} type="number" width={48} tickFormatter={(v) => formatNumber(v)} />
        <Tooltip content={<ChartTooltip />} cursor={{ strokeDasharray: "3 3" }} />
        <Scatter data={data} fill="var(--chart-1)" fillOpacity={0.75} />
      </ScatterChart>
    );
  } else if (plan.kind === "bar" && plan.horizontal) {
    chart = (
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }} barCategoryGap={6}>
        <CartesianGrid {...gridProps} horizontal={false} vertical />
        <XAxis {...axisProps} type="number" tickFormatter={(v) => formatNumber(v)} />
        <YAxis {...axisProps} type="category" dataKey={plan.x} width={112} tickFormatter={(v) => shortLabel(v, 16)} interval={0} />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.5 }} />
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} fill={s.color} radius={[0, 4, 4, 0]} maxBarSize={22}>
            {plan.ys.length === 1 &&
              data.map((entry) => (
                <Cell
                  key={String(entry[plan.x])}
                  fill={highlight && String(entry[plan.x]) === highlight ? "var(--chart-3)" : s.color}
                />
              ))}
          </Bar>
        ))}
      </BarChart>
    );
  } else if (plan.kind === "bar") {
    chart = (
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid {...gridProps} />
        <XAxis {...axisProps} dataKey={plan.x} tickFormatter={xFormatter} minTickGap={12} />
        <YAxis {...axisProps} width={48} tickFormatter={(v) => formatNumber(v)} />
        <Tooltip content={<ChartTooltip labelFormatter={xFormatter} />} cursor={{ fill: "var(--accent)", opacity: 0.5 }} />
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} fill={s.color} radius={[4, 4, 0, 0]} maxBarSize={40}>
            {plan.ys.length === 1 &&
              data.map((entry) => (
                <Cell
                  key={String(entry[plan.x])}
                  fill={highlight && String(entry[plan.x]) === highlight ? "var(--chart-3)" : s.color}
                />
              ))}
          </Bar>
        ))}
      </BarChart>
    );
  } else if (plan.kind === "area") {
    chart = (
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
        <defs>
          {series.map((s) => (
            <linearGradient key={s.key} id={`fill-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={s.color} stopOpacity={0.28} />
              <stop offset="100%" stopColor={s.color} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid {...gridProps} />
        <XAxis {...axisProps} dataKey={plan.x} tickFormatter={xFormatter} minTickGap={24} />
        <YAxis {...axisProps} width={48} tickFormatter={(v) => formatNumber(v)} />
        <Tooltip content={<ChartTooltip labelFormatter={xFormatter} />} />
        {series.map((s) => (
          <Area key={s.key} type="monotone" dataKey={s.key} stroke={s.color} strokeWidth={1.8} fill={`url(#fill-${s.key})`} />
        ))}
      </AreaChart>
    );
  } else {
    chart = (
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid {...gridProps} />
        <XAxis {...axisProps} dataKey={plan.x} tickFormatter={xFormatter} minTickGap={24} />
        <YAxis {...axisProps} width={48} tickFormatter={(v) => formatNumber(v)} />
        <Tooltip content={<ChartTooltip labelFormatter={xFormatter} />} />
        {series.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color} strokeWidth={1.8} dot={data.length <= 24 ? { r: 2.5 } : false} activeDot={{ r: 4 }} />
        ))}
      </LineChart>
    );
  }

  const barHeight = plan.kind === "bar" && plan.horizontal ? Math.max(height, Math.min(data.length, 25) * 26 + 24) : height;
  return (
    <div className={cn("space-y-2", className)}>
      {(showSwitch && plan.options.length > 1) || series.length > 1 ? (
        <div className="flex items-center justify-between gap-2">
          {series.length > 1 ? <Legend items={series.map((s) => ({ label: humanize(s.key), color: s.color }))} /> : <span />}
          {showSwitch && <KindSwitch plan={plan} onChange={setKind} />}
        </div>
      ) : null}
      <div style={{ height: barHeight }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          {chart}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function canChart(columns: string[], rows: Row[]): boolean {
  return planChart(columns, rows) !== null;
}
