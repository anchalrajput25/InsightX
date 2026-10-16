"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, AlertTriangle, Coins, Gauge, HeartPulse, Layers, MessageSquareText, ShieldCheck, Siren, Timer, Zap } from "lucide-react";
import { axisProps, ChartTooltip, gridProps, Legend } from "@/components/charts/chart-kit";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { Delta, Meter, pct, secs, Segmented } from "@/components/llmops/llmops-kit";
import { Badge } from "@/components/ui/badge";
import { llmopsApi, type MonitorWindow, type RouteHealth, type SloRow } from "@/lib/api/llmops";
import { formatDateTime, formatNumber, humanize, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const WINDOWS: { value: MonitorWindow; label: string }[] = [
  { value: "1h", label: "1 hour" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];

function tick(window: MonitorWindow) {
  return (value: unknown) => {
    const date = new Date(Number(value) * 1000);
    if (window === "1h" || window === "24h") return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
    return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };
}

const STATE_STYLE: Record<RouteHealth["state"], { label: string; variant: "success" | "warning" | "critical" | "outline" | "info" }> = {
  healthy: { label: "Healthy", variant: "success" },
  degraded: { label: "Degraded", variant: "warning" },
  failing: { label: "Failing", variant: "critical" },
  blocked: { label: "Blocked", variant: "critical" },
  no_key: { label: "No key", variant: "outline" },
  idle: { label: "Idle", variant: "info" },
};

function SloCard({ slo }: { slo: SloRow }) {
  const value = slo.unit === "ratio" ? pct(slo.value, 1) : secs(slo.value);
  const target = slo.unit === "ratio" ? pct(slo.target, 1) : secs(slo.target);
  const budget = slo.budget_used;
  return (
    <SpotlightCard className="rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{slo.label}</p>
        {slo.met === null ? <Badge variant="outline">No data</Badge> : slo.met ? <Badge variant="success">Met</Badge> : <Badge variant="critical">Missed</Badge>}
      </div>
      <p className="tabular mt-2 text-2xl font-semibold">{value}</p>
      <p className="text-xs text-muted-foreground">
        Target {slo.higher_is_better ? "at least" : "at most"} {target}
      </p>
      {budget !== undefined && (
        <div className="mt-3 space-y-1">
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>Error budget used</span>
            <span className="tabular">{Math.round(budget * 100)}%</span>
          </div>
          <span className="block h-1.5 rounded-full bg-muted">
            <span
              className={cn("block h-1.5 rounded-full transition-[width] duration-700", budget >= 1 ? "bg-critical" : budget >= 0.7 ? "bg-warning" : "bg-success")}
              style={{ width: `${Math.min(100, budget * 100)}%` }}
            />
          </span>
          <p className="text-[11px] text-muted-foreground">
            {slo.failures} failed of {formatNumber(slo.allowed_failures)} allowed
          </p>
        </div>
      )}
    </SpotlightCard>
  );
}

export function MonitorScreen() {
  const [window, setWindow] = React.useState<MonitorWindow>("24h");
  const data = useQuery({
    queryKey: ["llmops", "monitor", window],
    queryFn: () => llmopsApi.monitor(window),
    refetchInterval: 15_000,
  });
  const k = data.data?.kpis;
  const prev = data.data?.previous;
  const points = data.data?.series ?? [];
  const format = tick(window);
  const hasTraffic = points.some((p) => p.questions || p.ai_calls);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="Monitor"
        description="Live health of answering: success, speed, model errors, hallucinations and SLOs. Refreshes every 15 seconds."
        actions={
          <div className="flex items-center gap-3">
            {data.data && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className="relative flex size-2">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60" />
                  <span className="relative inline-flex size-2 rounded-full bg-success" />
                </span>
                Live, updated {timeAgo(data.data.generated_at)}
              </span>
            )}
            <Segmented label="Window" value={window} options={WINDOWS} onChange={setWindow} />
          </div>
        }
      />

      {data.isPending ? (
        <CardsSkeleton count={8} />
      ) : data.isError ? (
        <ErrorState error={data.error} onRetry={() => void data.refetch()} />
      ) : (
        <>
          <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Questions" value={formatNumber(k?.questions)} icon={MessageSquareText} hint={<Delta now={k?.questions} before={prev?.questions} asPercentPoints={false} />} />
            <StatCard label="Answered without error" value={pct(k?.success_rate, 1)} icon={ShieldCheck} tone={k?.success_rate !== null && (k?.success_rate ?? 1) < 0.9 ? "warning" : "success"} hint={<Delta now={k?.success_rate} before={prev?.success_rate} />} />
            <StatCard label="Answer time p50 / p95" value={secs(k?.p50_seconds)} icon={Timer} hint={`p95 ${secs(k?.p95_seconds)}`} />
            <StatCard label="Model call errors" value={pct(k?.ai_error_rate, 1)} icon={Zap} tone={(k?.ai_error_rate ?? 0) > 0.1 ? "critical" : "default"} hint={`${formatNumber(k?.ai_calls)} calls`} />
            <StatCard label="Hallucination rate" value={pct(k?.hallucination_rate, 1)} icon={AlertTriangle} tone={(k?.hallucination_rate ?? 0) > 0.05 ? "critical" : "success"} hint={<Delta now={k?.hallucination_rate} before={prev?.hallucination_rate} higherIsBetter={false} />} />
            <StatCard label="Grounding score" value={pct(k?.mean_faithfulness)} icon={Gauge} tone="primary" hint={`${formatNumber(k?.checked_answers)} answers checked`} />
            <StatCard label="Answered from cache" value={pct(k?.cache_hit_rate)} icon={Layers} tone="success" hint="Free and instant" />
            <StatCard label="Tokens" value={formatNumber(k?.tokens)} icon={Coins} hint={`Cost $${(k?.cost_usd ?? 0).toFixed(4)}`} />
          </div>

          {!hasTraffic && (
            <EmptyState
              compact
              icon={Activity}
              title="No traffic in this window"
              description={
                <>
                  Ask a question on the <Link href="/ask" className="text-primary underline">Ask</Link> page and it shows up here within seconds.
                </>
              }
            />
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border bg-card p-4">
              <SectionTitle>Traffic and failures</SectionTitle>
              <Legend items={[{ label: "Questions", color: "var(--chart-1)" }, { label: "Failed", color: "var(--critical)" }, { label: "Flagged answers", color: "var(--warning)" }]} />
              <div className="mt-3 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid {...gridProps} />
                    <XAxis {...axisProps} dataKey="t" tickFormatter={format} minTickGap={24} />
                    <YAxis {...axisProps} width={32} allowDecimals={false} />
                    <Tooltip content={<ChartTooltip labelFormatter={(v) => formatDateTime(Number(v))} />} cursor={{ fill: "var(--accent)", opacity: 0.4 }} />
                    <Bar dataKey="questions" name="Questions" stackId="q" fill="var(--chart-1)" maxBarSize={22} radius={[3, 3, 0, 0]} />
                    <Bar dataKey="errors" name="Failed" fill="var(--critical)" maxBarSize={22} radius={[3, 3, 0, 0]} />
                    <Bar dataKey="flagged" name="Flagged answers" fill="var(--warning)" maxBarSize={22} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
            <section className="rounded-xl border bg-card p-4">
              <SectionTitle>Answer time</SectionTitle>
              <Legend items={[{ label: "p50", color: "var(--chart-2)" }, { label: "p95", color: "var(--chart-4)", dashed: true }]} />
              <div className="mt-3 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="mon-p50" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...gridProps} />
                    <XAxis {...axisProps} dataKey="t" tickFormatter={format} minTickGap={24} />
                    <YAxis {...axisProps} width={36} unit="s" />
                    <Tooltip content={<ChartTooltip labelFormatter={(v) => formatDateTime(Number(v))} />} />
                    <Area type="monotone" dataKey="p50_seconds" name="p50 seconds" stroke="var(--chart-2)" fill="url(#mon-p50)" strokeWidth={2} dot={{ r: 3, fill: "var(--chart-2)" }} connectNulls />
                    <Line type="monotone" dataKey="p95_seconds" name="p95 seconds" stroke="var(--chart-4)" strokeDasharray="4 3" strokeWidth={2} dot={{ r: 3, fill: "var(--chart-4)" }} connectNulls />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </section>
            <section className="rounded-xl border bg-card p-4">
              <SectionTitle>Model calls and tokens</SectionTitle>
              <Legend items={[{ label: "Calls", color: "var(--chart-3)" }, { label: "Errors", color: "var(--critical)" }, { label: "Cache hits", color: "var(--chart-5)" }]} />
              <div className="mt-3 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid {...gridProps} />
                    <XAxis {...axisProps} dataKey="t" tickFormatter={format} minTickGap={24} />
                    <YAxis {...axisProps} width={32} allowDecimals={false} />
                    <Tooltip content={<ChartTooltip labelFormatter={(v) => formatDateTime(Number(v))} />} />
                    <Area type="monotone" dataKey="ai_calls" name="Calls" stackId="c" stroke="var(--chart-3)" fill="var(--chart-3)" fillOpacity={0.25} />
                    <Area type="monotone" dataKey="cache_hits" name="Cache hits" stackId="c" stroke="var(--chart-5)" fill="var(--chart-5)" fillOpacity={0.25} />
                    <Area type="monotone" dataKey="ai_errors" name="Errors" stroke="var(--critical)" fill="var(--critical)" fillOpacity={0.15} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </section>
            <section className="rounded-xl border bg-card p-4">
              <SectionTitle aside={<span className="text-[11px] text-muted-foreground">mean seconds per step</span>}>Where time goes</SectionTitle>
              {(data.data?.stages ?? []).length === 0 ? (
                <p className="py-16 text-center text-xs text-muted-foreground">No traced steps yet.</p>
              ) : (
                <ul className="space-y-2">
                  {(() => {
                    const stages = (data.data?.stages ?? []).slice(0, 9);
                    const max = Math.max(...stages.map((s) => s.mean_seconds), 0.001);
                    return stages.map((stage) => (
                      <li key={stage.stage} className="grid grid-cols-[130px_1fr_56px] items-center gap-3 text-xs">
                        <span className="truncate text-muted-foreground" title={stage.stage}>
                          {humanize(stage.stage)}
                        </span>
                        <span className="h-2 rounded-full bg-muted">
                          <span className="block h-2 rounded-full bg-[linear-gradient(90deg,var(--chart-1),var(--chart-2))]" style={{ width: `${(stage.mean_seconds / max) * 100}%` }} />
                        </span>
                        <span className="tabular text-right">{secs(stage.mean_seconds)}</span>
                      </li>
                    ));
                  })()}
                </ul>
              )}
            </section>
          </div>

          <section>
            <SectionTitle aside={<span className="text-[11px] text-muted-foreground">Targets can be changed below the cards</span>}>Service level objectives</SectionTitle>
            <div className="fx-stagger grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {(data.data?.slo ?? []).map((slo) => (
                <SloCard key={slo.key} slo={slo} />
              ))}
            </div>
            <SloEditor onSaved={() => void data.refetch()} />
          </section>

          <section>
            <SectionTitle>Model health</SectionTitle>
            <div className="overflow-x-auto rounded-xl border bg-card">
              <table className="w-full text-sm">
                <thead className="bg-surface text-xs text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2 text-left font-medium">Model</th>
                    <th className="px-3 py-2 text-left font-medium">State</th>
                    <th className="px-3 py-2 text-right font-medium">Calls</th>
                    <th className="w-40 px-3 py-2 text-left font-medium">Success</th>
                    <th className="hidden px-3 py-2 text-right font-medium md:table-cell">p50 / p95</th>
                    <th className="hidden px-3 py-2 text-left font-medium lg:table-cell">Errors</th>
                    <th className="hidden px-3 py-2 text-right font-medium sm:table-cell">Today</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.data?.routes ?? []).map((route) => (
                    <tr key={route.route} className="border-t">
                      <td className="max-w-64 px-4 py-2">
                        <span className="block truncate font-mono text-[12px]" title={route.route}>
                          {route.route}
                        </span>
                        <span className="text-[11px] text-muted-foreground capitalize">
                          {route.provider}
                          {route.tier ? ` · ${route.tier}` : ""}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <Badge variant={STATE_STYLE[route.state].variant} title={route.blocked_reason}>
                          {STATE_STYLE[route.state].label}
                        </Badge>
                      </td>
                      <td className="tabular px-3 py-2 text-right">{formatNumber(route.calls)}</td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Meter value={route.success_rate} />
                          <span className="tabular w-10 text-right text-xs">{pct(route.success_rate)}</span>
                        </div>
                      </td>
                      <td className="tabular hidden px-3 py-2 text-right text-xs md:table-cell">
                        {secs(route.p50_seconds)} / {secs(route.p95_seconds)}
                      </td>
                      <td className="hidden px-3 py-2 text-xs text-muted-foreground lg:table-cell">
                        {Object.entries(route.errors).length === 0 ? "-" : Object.entries(route.errors).map(([kind, count]) => `${humanize(kind)} ${count}`).join(", ")}
                      </td>
                      <td className="tabular hidden px-3 py-2 text-right text-xs sm:table-cell">
                        {route.requests_today ?? "-"}
                        {route.rpd_limit ? ` / ${formatNumber(route.rpd_limit)}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section>
              <SectionTitle>Incidents</SectionTitle>
              {(data.data?.incidents ?? []).length === 0 ? (
                <EmptyState compact icon={HeartPulse} title="No spikes found" description="A spike is a time slot far above the usual level for latency, failures or flagged answers." />
              ) : (
                <ul className="space-y-2">
                  {data.data!.incidents.map((item, index) => (
                    <li key={index} className="flex items-start gap-3 rounded-xl border border-critical/25 bg-critical/[0.05] px-4 py-3">
                      <Siren className="mt-0.5 size-4 shrink-0 text-critical" />
                      <div className="min-w-0 text-sm">
                        <p className="font-medium">{item.label}</p>
                        <p className="text-xs text-muted-foreground">
                          {formatDateTime(item.t)}: {item.metric.includes("seconds") ? secs(item.value) : formatNumber(item.value)}, usual{" "}
                          {item.metric.includes("seconds") ? secs(item.usual) : formatNumber(item.usual)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <SectionTitle>Recent failures</SectionTitle>
              {(data.data?.failures ?? []).length === 0 ? (
                <EmptyState compact icon={ShieldCheck} title="No failed questions" description="Questions that ended in an error show up here with the reason." />
              ) : (
                <ul className="divide-y overflow-hidden rounded-xl border bg-card">
                  {data.data!.failures.map((item) => (
                    <li key={item.id}>
                      <Link href={`/traces?id=${item.id}`} className="block px-4 py-3 transition-colors hover:bg-accent/50">
                        <p className="truncate text-sm">{item.question}</p>
                        <p className="mt-0.5 line-clamp-1 text-xs text-critical">{item.error || humanize(item.status.toLowerCase())}</p>
                        <p className="text-[11px] text-muted-foreground">{formatDateTime(item.ts)}</p>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function SloEditor({ onSaved }: { onSaved: () => void }) {
  const targets = useQuery({ queryKey: ["llmops", "slo"], queryFn: () => llmopsApi.slo() });
  const [draft, setDraft] = React.useState<Record<string, string>>({});
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    if (targets.data)
      setDraft({
        success_rate: String(Math.round(targets.data.success_rate * 1000) / 10),
        p95_seconds: String(targets.data.p95_seconds),
        ai_error_rate: String(Math.round(targets.data.ai_error_rate * 1000) / 10),
        hallucination_rate: String(Math.round(targets.data.hallucination_rate * 1000) / 10),
      });
  }, [targets.data]);

  const fields: { key: string; label: string; unit: string }[] = [
    { key: "success_rate", label: "Answered at least", unit: "%" },
    { key: "p95_seconds", label: "p95 at most", unit: "s" },
    { key: "ai_error_rate", label: "Model errors at most", unit: "%" },
    { key: "hallucination_rate", label: "Hallucinations at most", unit: "%" },
  ];

  async function save() {
    setSaving(true);
    setError("");
    try {
      await llmopsApi.saveSlo({
        success_rate: Number(draft.success_rate) / 100,
        p95_seconds: Number(draft.p95_seconds),
        ai_error_rate: Number(draft.ai_error_rate) / 100,
        hallucination_rate: Number(draft.hallucination_rate) / 100,
      });
      onSaved();
      void targets.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  if (!targets.data) return null;
  return (
    <div className="mt-3 flex flex-wrap items-end gap-3 rounded-xl border border-dashed p-3">
      {fields.map((field) => (
        <label key={field.key} className="space-y-1 text-[11px] text-muted-foreground">
          <span className="block">{field.label}</span>
          <span className="flex items-center gap-1">
            <input
              type="number"
              step="any"
              value={draft[field.key] ?? ""}
              onChange={(event) => setDraft((d) => ({ ...d, [field.key]: event.target.value }))}
              className="tabular h-8 w-20 rounded-md border bg-background px-2 text-sm text-foreground"
            />
            {field.unit}
          </span>
        </label>
      ))}
      <button type="button" onClick={() => void save()} disabled={saving} className="h-8 cursor-pointer rounded-md border px-3 text-xs font-medium hover:bg-accent disabled:opacity-50">
        {saving ? "Saving" : "Save targets"}
      </button>
      {error && <p className="text-xs text-critical">{error}</p>}
    </div>
  );
}
