"use client";

// What-if page: ask "what if prices rise 10%?", edit the changes it found,
// see baseline vs scenario, the impact by segment, a sensitivity curve and
// a forecast with its band. Every number comes from read-only SQL on the
// connected database (backend/whatif).

import * as React from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  Code2,
  Database,
  Info,
  Layers,
  LineChart as LineChartIcon,
  Loader2,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Target,
  TrendingUp,
  X,
} from "lucide-react";
import { axisProps, ChartTooltip, gridProps, Legend, shortDate, shortLabel } from "@/components/charts/chart-kit";
import { CodeBlock } from "@/components/common/code-block";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { useConnection } from "@/components/providers/connection-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  whatifApi,
  type ForecastReport,
  type LeverSpec,
  type ScenarioSpec,
  type SweepReport,
  type WhatIfReport,
} from "@/lib/api/whatif";
import { formatNumber, formatPercent, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "What if prices rise 10%?",
  "What if we drop the 2 worst-selling products?",
  "What if discounts are capped at 10% and orders grow 5%?",
  "Forecast revenue for the next quarter if prices rise 5%",
];

const PARAMETER: Partial<Record<LeverSpec["type"], keyof LeverSpec>> = {
  scale: "percent",
  volume: "percent",
  shift: "amount",
  set: "value",
  cap: "limit",
  floor: "limit",
};

const PARAMETER_UNIT: Record<string, string> = { percent: "%", amount: "", value: "", limit: "" };

function tone(value: number | null | undefined): "success" | "critical" | "default" {
  if (value === null || value === undefined || value === 0) return "default";
  return value > 0 ? "success" : "critical";
}

function segmentText(lever: LeverSpec): string {
  const where = lever.where;
  if (!where) return "";
  const column = where.column.split(".").pop() ?? where.column;
  if (where.rank) return `${where.rank} ${where.n} ${column}`;
  const values = (where.values ?? []).slice(0, 3).join(", ");
  return `${column} ${where.op ?? "="} ${values}`;
}

function leverTitle(lever: LeverSpec): string {
  const column = lever.column ? (lever.column.split(".").pop() ?? lever.column) : "value per row";
  switch (lever.type) {
    case "scale":
      return `Change ${column}`;
    case "shift":
      return `Shift ${column}`;
    case "set":
      return `Set ${column}`;
    case "cap":
      return `Cap ${column}`;
    case "floor":
      return `Floor ${column}`;
    case "volume":
      return "Change rows";
    default:
      return "Remove rows";
  }
}

// ------------------------------------------------------------ inputs

function QuestionBox({ busy, onAsk }: { busy: boolean; onAsk: (question: string) => void }) {
  const [text, setText] = React.useState("");
  return (
    <div className="space-y-3">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) onAsk(text.trim());
        }}
        className="relative overflow-hidden rounded-xl border bg-card p-1.5 pl-4 focus-within:border-foreground/20"
      >
        <div aria-hidden className="pointer-events-none absolute -right-20 -top-20 size-48 rounded-full bg-primary/10 blur-3xl" />
        <div className="relative flex items-center gap-3">
          <SlidersHorizontal className="size-4 shrink-0 text-primary" />
          <label htmlFor="whatif-question" className="sr-only">
            Describe a scenario
          </label>
          <input
            id="whatif-question"
            value={text}
            onChange={(event) => setText(event.target.value)}
            disabled={busy}
            placeholder="What if prices rise 10% and we drop the 2 worst sellers?"
            className="h-10 min-w-0 flex-1 bg-transparent text-sm placeholder:text-muted-foreground/70 focus:outline-none"
            maxLength={1000}
          />
          <Button type="submit" size="sm" disabled={!text.trim() || busy}>
            {busy ? <Loader2 className="animate-spin" /> : <ArrowUp />}
            {busy ? "Working…" : "Run scenario"}
          </Button>
        </div>
      </form>
      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            disabled={busy}
            onClick={() => {
              setText(example);
              onAsk(example);
            }}
            className="rounded-full border bg-card/60 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/15 hover:text-foreground disabled:opacity-50"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}

function LeverChips({
  spec,
  busy,
  dirty,
  onChange,
  onRun,
  onReset,
}: {
  spec: ScenarioSpec;
  busy: boolean;
  dirty: boolean;
  onChange: (spec: ScenarioSpec) => void;
  onRun: () => void;
  onReset: () => void;
}) {
  const update = (index: number, patch: Partial<LeverSpec>) =>
    onChange({ ...spec, levers: spec.levers.map((lever, i) => (i === index ? { ...lever, ...patch } : lever)) });
  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">Changes in this scenario</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Metric: <span className="font-mono">{spec.metric_name}</span> · edit the numbers and run again (no AI call)
          </p>
        </div>
        <div className="flex gap-2">
          {dirty && (
            <Button size="sm" variant="ghost" onClick={onReset} disabled={busy}>
              <RotateCcw /> Undo edits
            </Button>
          )}
          <Button size="sm" onClick={onRun} disabled={busy || !dirty || spec.levers.length === 0}>
            {busy ? <Loader2 className="animate-spin" /> : <ArrowRight />} Run again
          </Button>
        </div>
      </div>
      {spec.filters && (
        <div className="mb-3 flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Only rows where</span>
          <code className="max-w-xl truncate rounded bg-muted px-1.5 py-0.5 font-mono">{spec.filters}</code>
          <button
            type="button"
            aria-label="Remove this filter"
            onClick={() => onChange({ ...spec, filters: null })}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}
      {spec.levers.length === 0 && <p className="text-xs text-muted-foreground">No changes: add one by asking a new question.</p>}
      <div className="flex flex-wrap gap-2">
        {spec.levers.map((lever, index) => {
          const parameter = PARAMETER[lever.type];
          return (
            <div key={index} className="flex items-center gap-2 rounded-lg border bg-background/40 py-1.5 pl-3 pr-1.5 text-xs">
              <span className="font-medium">{leverTitle(lever)}</span>
              {parameter && (
                <span className="flex items-center gap-1">
                  <input
                    type="number"
                    step="any"
                    aria-label={`${leverTitle(lever)} ${parameter}`}
                    value={Number(lever[parameter] ?? 0)}
                    onChange={(event) => update(index, { [parameter]: Number(event.target.value) } as Partial<LeverSpec>)}
                    className="h-7 w-20 rounded-md border bg-transparent px-2 text-right font-mono tabular focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <span className="text-muted-foreground">{PARAMETER_UNIT[parameter as string]}</span>
                </span>
              )}
              {lever.type === "scale" && (
                <label className="flex items-center gap-1 text-muted-foreground">
                  elasticity
                  <input
                    type="number"
                    step="0.1"
                    min={-10}
                    max={10}
                    value={Number(lever.elasticity ?? 0)}
                    onChange={(event) => update(index, { elasticity: Number(event.target.value) })}
                    className="h-7 w-16 rounded-md border bg-transparent px-2 text-right font-mono tabular focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </label>
              )}
              {lever.where && (
                <Badge variant="outline" className="max-w-56 truncate">
                  {segmentText(lever)}
                </Badge>
              )}
              <button
                type="button"
                aria-label="Remove this change"
                onClick={() => onChange({ ...spec, levers: spec.levers.filter((_, i) => i !== index) })}
                className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ------------------------------------------------------------ results

function Summary({ report }: { report: WhatIfReport }) {
  const Arrow = (report.delta ?? 0) >= 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <StatCard label={`${humanize(report.metric.name)} now`} value={formatNumber(report.baseline)} icon={Target} hint={`${formatNumber(report.rows)} rows`} />
      <StatCard label="With the changes" value={formatNumber(report.scenario)} icon={Sparkles} tone="primary" hint={report.levers.map((l) => l.label).join(" · ")} />
      <StatCard
        label="Change"
        value={formatPercent(report.delta_pct)}
        icon={Arrow}
        tone={tone(report.delta)}
        hint={`${(report.delta ?? 0) >= 0 ? "+" : ""}${formatNumber(report.delta)}`}
      />
    </div>
  );
}

function LeverEffects({ report }: { report: WhatIfReport }) {
  if (report.lever_effects.length < 2) return null;
  const largest = Math.max(...report.lever_effects.map((effect) => Math.abs(effect.delta ?? 0)), 1);
  return (
    <section className="rounded-xl border bg-card p-4">
      <h3 className="text-sm font-medium">What each change does on its own</h3>
      <p className="mt-0.5 text-xs text-muted-foreground">Each change applied alone; the rest is how they interact.</p>
      <div className="mt-3 space-y-2">
        {report.lever_effects.map((effect) => (
          <div key={effect.label} className="grid grid-cols-[minmax(0,1fr)_7rem_4.5rem] items-center gap-3 text-xs">
            <div className="min-w-0">
              <p className="truncate">{effect.label}</p>
              <div className="mt-1 h-1.5 rounded-full bg-muted">
                <div
                  className={cn("h-full rounded-full", (effect.delta ?? 0) >= 0 ? "bg-success" : "bg-critical")}
                  style={{ width: `${(Math.abs(effect.delta ?? 0) / largest) * 100}%` }}
                />
              </div>
            </div>
            <span className="tabular text-right font-mono">{formatNumber(effect.delta)}</span>
            <span className="tabular text-right font-mono text-muted-foreground">{formatPercent(effect.delta_pct)}</span>
          </div>
        ))}
        {report.interaction !== null && (
          <div className="grid grid-cols-[minmax(0,1fr)_7rem_4.5rem] gap-3 border-t pt-2 text-xs text-muted-foreground">
            <span>Interaction between the changes</span>
            <span className="tabular text-right font-mono">{formatNumber(report.interaction)}</span>
            <span />
          </div>
        )}
      </div>
    </section>
  );
}

function ImpactBySegment({ report }: { report: WhatIfReport }) {
  const breakdown = report.breakdown;
  if (!breakdown || breakdown.rows.length === 0) return null;
  const data = breakdown.rows.map((row) => ({ ...row, label: row.segment }));
  return (
    <section className="rounded-xl border bg-card">
      <header className="px-4 pb-2 pt-3.5">
        <h3 className="text-sm font-medium">Impact by {humanize(breakdown.dimension.split(".").pop() ?? breakdown.dimension)}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Change per segment, largest first · {breakdown.segments} segments
          {breakdown.unchanged_segments ? `, ${breakdown.unchanged_segments} unchanged` : ""}
        </p>
      </header>
      <div className="px-4 pb-4" style={{ height: Math.max(200, data.length * 28 + 30) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 4 }} barCategoryGap={5}>
            <CartesianGrid {...gridProps} horizontal={false} vertical />
            <XAxis {...axisProps} type="number" tickFormatter={(v) => formatNumber(v)} />
            <YAxis {...axisProps} type="category" dataKey="label" width={130} tickFormatter={(v) => shortLabel(v, 18)} interval={0} />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.5 }} />
            <ReferenceLine x={0} stroke="var(--chart-axis)" />
            <Bar dataKey="delta" name="Change" radius={[0, 4, 4, 0]} maxBarSize={18}>
              {data.map((row) => (
                <Cell key={row.segment} fill={(row.delta ?? 0) >= 0 ? "var(--success)" : "var(--critical)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function Sensitivity({
  sweep,
  busy,
  onElasticity,
}: {
  sweep: SweepReport;
  busy: boolean;
  onElasticity: (value: number) => void;
}) {
  const [elasticity, setElasticity] = React.useState(sweep.elasticity ?? 0);
  React.useEffect(() => setElasticity(sweep.elasticity ?? 0), [sweep.elasticity]);
  const unit = sweep.parameter === "percent" ? "%" : "";
  return (
    <section className="rounded-xl border bg-card">
      <header className="flex flex-wrap items-end justify-between gap-3 px-4 pb-2 pt-3.5">
        <div>
          <h3 className="text-sm font-medium">Sensitivity</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">How the result moves as one change varies: {sweep.lever}</p>
        </div>
        {sweep.parameter === "percent" && sweep.lever.length > 0 && (
          <form
            className="flex items-center gap-2 text-xs text-muted-foreground"
            onSubmit={(event) => {
              event.preventDefault();
              onElasticity(elasticity);
            }}
          >
            <label htmlFor="sweep-elasticity">Elasticity (demand reaction)</label>
            <input
              id="sweep-elasticity"
              type="number"
              step="0.1"
              min={-10}
              max={10}
              value={elasticity}
              onChange={(event) => setElasticity(Number(event.target.value))}
              className="h-7 w-16 rounded-md border bg-transparent px-2 text-right font-mono tabular focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <Button size="xs" variant="outline" type="submit" disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <ArrowRight />} Apply
            </Button>
          </form>
        )}
      </header>
      <div className="h-60 px-4 pb-4">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={sweep.points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis {...axisProps} dataKey="value" tickFormatter={(v) => `${v > 0 ? "+" : ""}${formatNumber(v)}${unit}`} />
            <YAxis {...axisProps} width={56} tickFormatter={(v) => formatNumber(v)} />
            <Tooltip content={<ChartTooltip labelFormatter={(v) => `${formatNumber(v)}${unit}`} />} />
            {sweep.baseline !== null && <ReferenceLine y={sweep.baseline} stroke="var(--chart-axis)" strokeDasharray="4 4" />}
            <ReferenceLine x={sweep.current} stroke="var(--chart-3)" strokeDasharray="3 3" />
            <Line type="monotone" dataKey="scenario" name="Result" stroke="var(--chart-1)" strokeWidth={2} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="px-4 pb-3">
        <Legend
          items={[
            { label: "Result at each value", color: "var(--chart-1)" },
            { label: "Today", color: "var(--chart-axis)", dashed: true },
            { label: "Your scenario", color: "var(--chart-3)", dashed: true },
          ]}
        />
      </div>
    </section>
  );
}

function ForecastPanel({
  forecast,
  busy,
  onHorizon,
}: {
  forecast: ForecastReport | null;
  busy: boolean;
  onHorizon: (horizon: number) => void;
}) {
  const horizons = [3, 6, 12];
  const data = forecast
    ? [
        ...forecast.history.map((point) => ({ period: point.period, actual: point.baseline, scenario_actual: point.scenario })),
        ...forecast.forecast.map((point) => ({
          period: point.period,
          forecast: point.yhat,
          band: [point.lower, point.upper] as [number | null, number | null],
          scenario: point.scenario ?? null,
        })),
      ]
    : [];
  const backtest = forecast?.backtest;
  const beats = backtest && backtest.mape !== null && backtest.naive_mape !== null && backtest.mape < backtest.naive_mape;
  return (
    <section className="rounded-xl border bg-card">
      <header className="flex flex-wrap items-end justify-between gap-3 px-4 pb-2 pt-3.5">
        <div>
          <h3 className="text-sm font-medium">Forecast</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {forecast
              ? `${forecast.model} · next ${forecast.horizon} ${forecast.grain}s · 95% band`
              : "Project the metric forward, with and without the changes"}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {horizons.map((horizon) => (
            <Button
              key={horizon}
              size="xs"
              variant={forecast?.horizon === horizon ? "secondary" : "outline"}
              disabled={busy}
              onClick={() => onHorizon(horizon)}
            >
              {horizon} periods
            </Button>
          ))}
          {busy && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        </div>
      </header>
      {!forecast ? (
        <div className="px-4 pb-5">
          <EmptyState
            compact
            icon={TrendingUp}
            title="No forecast yet"
            description="Pick how many periods to forecast. It needs a date column and at least 3 full periods of history."
          />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 px-4 pb-2">
            <Badge variant="outline">
              Next {forecast.horizon} {forecast.grain}s ({forecast.summary.aggregate}): {formatNumber(forecast.summary.baseline)}
            </Badge>
            {forecast.summary.scenario !== undefined && forecast.summary.scenario !== null && (
              <Badge variant="primary">
                With the changes: {formatNumber(forecast.summary.scenario)} ({formatPercent(forecast.summary.delta_pct)})
              </Badge>
            )}
            {backtest && backtest.mape !== null && (
              <Badge variant={beats ? "success" : "warning"}>
                Backtest error {backtest.mape.toFixed(1)}% vs naive {backtest.naive_mape?.toFixed(1) ?? "-"}% (last {backtest.holdout_periods})
              </Badge>
            )}
          </div>
          <div className="h-72 px-4 pb-4">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis {...axisProps} dataKey="period" tickFormatter={shortDate} minTickGap={24} />
                <YAxis {...axisProps} width={56} tickFormatter={(v) => formatNumber(v)} />
                <Tooltip content={<ChartTooltip labelFormatter={(v) => shortDate(v)} />} />
                <Area dataKey="band" name="95% band" stroke="none" fill="var(--chart-1)" fillOpacity={0.14} connectNulls={false} />
                <Line dataKey="actual" name="Actual" stroke="var(--chart-2)" strokeWidth={2} dot={false} />
                <Line dataKey="forecast" name="Forecast" stroke="var(--chart-1)" strokeWidth={2} strokeDasharray="5 4" dot={{ r: 2 }} />
                <Line dataKey="scenario" name="Forecast with changes" stroke="var(--chart-3)" strokeWidth={2} strokeDasharray="2 3" dot={{ r: 2 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="px-4 pb-3">
            <Legend
              items={[
                { label: "Actual", color: "var(--chart-2)" },
                { label: "Forecast", color: "var(--chart-1)", dashed: true },
                { label: "Forecast with changes", color: "var(--chart-3)", dashed: true },
                { label: "95% band", color: "var(--chart-1)" },
              ]}
            />
          </div>
        </>
      )}
    </section>
  );
}

function Assumptions({ assumptions, notes }: { assumptions: string[]; notes: string[] }) {
  return (
    <section className="rounded-xl border bg-card p-4">
      <h3 className="flex items-center gap-2 text-sm font-medium">
        <Info className="size-4 text-muted-foreground" /> Assumptions
      </h3>
      <ul className="mt-2 space-y-1.5 text-xs text-muted-foreground">
        {assumptions.map((sentence) => (
          <li key={sentence} className="flex gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground/60" />
            {sentence}
          </li>
        ))}
      </ul>
      {notes.length > 0 && (
        <>
          <h4 className="mt-3 text-xs font-medium">Notes</h4>
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function SqlPanel({ sql }: { sql: Record<string, string | undefined> }) {
  const [open, setOpen] = React.useState(false);
  const entries = Object.entries(sql).filter(([, value]) => value) as [string, string][];
  return (
    <section className="rounded-xl border bg-card p-4">
      <button type="button" onClick={() => setOpen(!open)} className="flex items-center gap-2 text-sm font-medium">
        <Code2 className="size-4 text-muted-foreground" /> {open ? "Hide" : "Show"} the SQL behind these numbers
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {entries.map(([name, value]) => (
            <CodeBlock key={name} code={value} label={humanize(name)} />
          ))}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------ screen

export function WhatIfScreen() {
  const { connection, ready, setConnection } = useConnection();
  const id = connection?.id ?? "";
  const [report, setReport] = React.useState<WhatIfReport | null>(null);
  const [forecast, setForecast] = React.useState<ForecastReport | null>(null);
  const [sweep, setSweep] = React.useState<SweepReport | null>(null);
  const [spec, setSpec] = React.useState<ScenarioSpec | null>(null);
  const [savedSpec, setSavedSpec] = React.useState<ScenarioSpec | null>(null);

  const onLost = React.useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.lostConnection) setConnection(null);
    },
    [setConnection],
  );

  const show = React.useCallback((result: WhatIfReport | ForecastReport) => {
    setSpec(result.spec);
    setSavedSpec(result.spec);
    if (result.kind === "forecast") {
      setReport(null);
      setSweep(null);
      setForecast(result);
      return;
    }
    setReport(result);
    setSweep(result.sensitivity ?? null);
    setForecast(result.forecast ?? null);
  }, []);

  const ask = useMutation({
    mutationFn: (question: string) => whatifApi.ask(id, question),
    onSuccess: show,
    onError: onLost,
  });
  const rerun = useMutation({
    mutationFn: (edited: ScenarioSpec) => whatifApi.rerun(id, edited),
    onSuccess: show,
    onError: (error) => {
      onLost(error);
      toast.error("Could not run that scenario", { description: errorMessage(error) });
    },
  });
  const sweepRun = useMutation({
    mutationFn: ({ index, elasticity }: { index: number; elasticity: number }) =>
      whatifApi.sweep(id, savedSpec as ScenarioSpec, index, elasticity),
    onSuccess: setSweep,
    onError: (error) => toast.error("Could not redo the sensitivity curve", { description: errorMessage(error) }),
  });
  const forecastRun = useMutation({
    mutationFn: (horizon: number) => whatifApi.forecast(id, savedSpec as ScenarioSpec, horizon),
    onSuccess: setForecast,
    onError: (error) => toast.error("Could not make a forecast", { description: errorMessage(error) }),
  });

  if (!ready) return null;
  if (!connection) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon={SlidersHorizontal}
          title="No database connected"
          description="What-if scenarios run on the database you connect, without changing it."
          action={
            <Button asChild>
              <Link href="/connect">
                <Database /> Connect a database
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  const busy = ask.isPending || rerun.isPending;
  const dirty = JSON.stringify(spec) !== JSON.stringify(savedSpec);
  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8 sm:px-8">
      <PageHeader
        title="What-if"
        description={`Test a change before making it: every number comes from read-only SQL on ${connection.label}.`}
      />
      <QuestionBox busy={busy} onAsk={(question) => ask.mutate(question)} />

      {busy && <CardsSkeleton count={3} />}
      {ask.isError && !busy && <ErrorState error={ask.error} />}

      {spec && !busy && (
        <LeverChips
          spec={spec}
          busy={rerun.isPending}
          dirty={dirty}
          onChange={setSpec}
          onRun={() => spec && rerun.mutate(spec)}
          onReset={() => setSpec(savedSpec)}
        />
      )}

      {report && !busy && (
        <>
          <Summary report={report} />
          <div className="grid gap-6 lg:grid-cols-2">
            <ImpactBySegment report={report} />
            <div className="space-y-6">
              <LeverEffects report={report} />
              {sweep && (
                <Sensitivity
                  sweep={sweep}
                  busy={sweepRun.isPending}
                  onElasticity={(elasticity) => sweepRun.mutate({ index: sweep.lever_index, elasticity })}
                />
              )}
            </div>
          </div>
        </>
      )}

      {(report || forecast) && !busy && (
        <>
          <SectionTitle aside={<Layers className="size-4 text-muted-foreground" />}>Looking ahead</SectionTitle>
          <ForecastPanel forecast={forecast} busy={forecastRun.isPending} onHorizon={(horizon) => forecastRun.mutate(horizon)} />
          <div className="grid gap-6 lg:grid-cols-2">
            <Assumptions
              assumptions={[...(report?.assumptions ?? []), ...(forecast?.assumptions ?? [])].filter(
                (sentence, index, all) => all.indexOf(sentence) === index,
              )}
              notes={[...(report?.notes ?? []), ...(forecast?.notes ?? [])].filter((note, index, all) => all.indexOf(note) === index)}
            />
            <SqlPanel sql={{ ...(report?.sql ?? {}), ...(forecast?.sql ?? {}) }} />
          </div>
        </>
      )}

      {!spec && !busy && !ask.isError && (
        <EmptyState
          icon={LineChartIcon}
          title="Ask a what-if question"
          description="Change prices, remove a segment, cap a value or add volume, then see the effect, which segments move, how sensitive it is, and a forecast."
        />
      )}
    </div>
  );
}
