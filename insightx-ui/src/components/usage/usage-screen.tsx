"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, CheckCircle2, Coins, Cpu, Gauge, KeyRound, Layers, MessageSquareText, Timer, Zap } from "lucide-react";
import { axisProps, ChartTooltip, gridProps, Legend, SERIES, shortDate } from "@/components/charts/chart-kit";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { monitoringApi } from "@/lib/api/monitoring";
import { formatNumber, humanize } from "@/lib/format";
import type { ModelRoute, UsageGroup } from "@/lib/types";
import { cn } from "@/lib/utils";

const PROVIDER_COLORS: Record<string, string> = {
  gemini: "var(--chart-1)",
  groq: "var(--chart-2)",
  openrouter: "var(--chart-4)",
  cache: "var(--chart-5)",
};

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
}

function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

function GroupTable({ rows, keyName, label }: { rows: UsageGroup[]; keyName: "route" | "job"; label: string }) {
  if (rows.length === 0) return <p className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">Nothing recorded in this period.</p>;
  const max = Math.max(...rows.map((row) => row.requests + row.cache_hits), 1);
  return (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow className="bg-surface hover:bg-surface">
            <TableHead>{label}</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead className="hidden text-right sm:table-cell">Failed</TableHead>
            <TableHead className="hidden text-right md:table-cell">Cache</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
            <TableHead className="hidden text-right md:table-cell">Typical time</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const name = String(row[keyName] ?? "-");
            return (
              <TableRow key={name}>
                <TableCell className="max-w-72">
                  <span className="block truncate font-mono text-[12px]" title={name}>
                    {keyName === "job" ? humanize(name) : name}
                  </span>
                  <span className="mt-1 block h-1 rounded-full bg-muted">
                    <span className="block h-1 rounded-full bg-primary/70" style={{ width: `${((row.requests + row.cache_hits) / max) * 100}%` }} />
                  </span>
                </TableCell>
                <TableCell className="tabular text-right">{formatNumber(row.requests)}</TableCell>
                <TableCell className={cn("tabular hidden text-right sm:table-cell", row.failed && "text-critical")}>{formatNumber(row.failed)}</TableCell>
                <TableCell className="tabular hidden text-right md:table-cell">{formatNumber(row.cache_hits)}</TableCell>
                <TableCell className="tabular text-right">{formatNumber(row.tokens)}</TableCell>
                <TableCell className="tabular hidden text-right md:table-cell">{ms(row.p50_latency_ms)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function RouteCard({ route }: { route: ModelRoute }) {
  const usedShare = route.rpd_limit ? Math.min(1, route.requests_today / route.rpd_limit) : 0;
  const cooling = route.cooldown_seconds > 0;
  const state = !route.key_configured
    ? { label: "No key", variant: "outline" as const }
    : route.blocked_reason
      ? { label: "Blocked", variant: "critical" as const }
      : cooling
        ? { label: `Cooling ${Math.ceil(route.cooldown_seconds)}s`, variant: "warning" as const }
        : { label: "Ready", variant: "success" as const };
  return (
    <li className="rounded-lg border bg-card p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-mono text-[12px]" title={route.route}>
            {route.model}
          </p>
          <p className="mt-0.5 text-[11px] capitalize text-muted-foreground">
            {route.provider} · #{route.position}
            {route.free ? " · free" : ""}
          </p>
        </div>
        <Badge variant={state.variant}>{state.label}</Badge>
      </div>
      <div className="mt-3 space-y-1">
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>Today</span>
          <span className="tabular">
            {formatNumber(route.requests_today)}
            {route.rpd_limit ? ` / ${formatNumber(route.rpd_limit)}` : ""}
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-muted">
          <div className={cn("h-1.5 rounded-full", usedShare > 0.9 ? "bg-critical" : usedShare > 0.6 ? "bg-warning" : "bg-success")} style={{ width: `${Math.max(usedShare * 100, route.requests_today ? 2 : 0)}%` }} />
        </div>
        <p className="text-[11px] text-muted-foreground">
          {route.successes} ok · {route.failures} failed{route.rpm_limit ? ` · ${route.rpm_limit}/min limit` : ""}
        </p>
        {(route.blocked_reason || route.last_error || route.cooldown_reason) && (
          <p className="line-clamp-2 text-[11px] text-critical" title={route.blocked_reason || route.last_error || route.cooldown_reason}>
            {route.blocked_reason || route.cooldown_reason || route.last_error}
          </p>
        )}
      </div>
    </li>
  );
}

export function UsageScreen() {
  const [days, setDays] = React.useState(30);
  const usage = useQuery({ queryKey: ["usage", days], queryFn: () => monitoringApi.usage(days) });
  const routes = useQuery({ queryKey: ["usage", "routes"], queryFn: () => monitoringApi.routes(), refetchInterval: 30_000 });

  const totals = usage.data?.totals;
  const byDay = usage.data?.by_day ?? [];
  const series = Array.from(new Set(byDay.flatMap((row) => Object.keys(row).filter((key) => key !== "day"))));
  const tiers = Array.from(new Set((routes.data?.routes ?? []).map((route) => route.tier)));

  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="AI usage"
        description="How many AI calls your questions needed, which free models answered, and how close each is to its daily limit."
        actions={
          <div role="radiogroup" aria-label="Period" className="flex gap-0.5 rounded-lg bg-muted/60 p-0.5">
            {[7, 30, 90].map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={days === value}
                onClick={() => setDays(value)}
                className={cn("h-7 rounded-md px-3 text-xs font-medium text-muted-foreground hover:text-foreground", days === value && "bg-card text-foreground shadow-sm")}
              >
                {value} days
              </button>
            ))}
          </div>
        }
      />

      {usage.isPending ? (
        <CardsSkeleton count={4} />
      ) : usage.isError ? (
        <ErrorState error={usage.error} onRetry={() => void usage.refetch()} />
      ) : (
        <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Questions" value={formatNumber(totals?.questions)} icon={MessageSquareText} hint={totals?.calls_per_question ? `${totals.calls_per_question} AI calls each` : undefined} />
          <StatCard label="AI calls" value={formatNumber(totals?.requests)} icon={Zap} hint={`${formatNumber(totals?.failed)} failed`} tone={totals?.failed ? "warning" : "default"} />
          <StatCard label="Answered from cache" value={percent(totals?.cache_hit_rate)} icon={Layers} hint={`${formatNumber(totals?.cache_hits)} lookups`} tone="success" />
          <StatCard
            label="Tokens"
            value={formatNumber((totals?.prompt_tokens ?? 0) + (totals?.completion_tokens ?? 0))}
            icon={Coins}
            hint={`Typical call ${ms(totals?.p50_latency_ms)} · cost $${(totals?.cost_usd ?? 0).toFixed(2)}`}
          />
        </div>
      )}

      <section className="space-y-3">
        <SectionTitle>Calls per day</SectionTitle>
        <div className="rounded-xl border bg-card p-4">
          {usage.isPending ? (
            <div className="h-64 animate-pulse rounded-lg bg-muted/40" />
          ) : byDay.length === 0 ? (
            <EmptyState compact icon={Cpu} title="No AI calls in this period" description="Calls show up here after you ask questions that need the AI." className="py-12" />
          ) : (
            <div className="space-y-3">
              <Legend items={series.map((key, index) => ({ label: humanize(key), color: PROVIDER_COLORS[key] ?? (SERIES[index % SERIES.length] as string) }))} />
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byDay} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid {...gridProps} />
                    <XAxis {...axisProps} dataKey="day" tickFormatter={shortDate} minTickGap={16} />
                    <YAxis {...axisProps} width={36} allowDecimals={false} />
                    <Tooltip content={<ChartTooltip labelFormatter={shortDate} />} cursor={{ fill: "var(--accent)", opacity: 0.5 }} />
                    {series.map((key, index) => (
                      <Bar key={key} dataKey={key} name={humanize(key)} stackId="calls" fill={PROVIDER_COLORS[key] ?? SERIES[index % SERIES.length]} maxBarSize={28} radius={index === series.length - 1 ? [3, 3, 0, 0] : 0} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="space-y-3">
          <SectionTitle>By model</SectionTitle>
          {usage.isPending ? <RowsSkeleton rows={4} /> : <GroupTable rows={usage.data?.by_route ?? []} keyName="route" label="Model route" />}
        </section>
        <section className="space-y-3">
          <SectionTitle>By step</SectionTitle>
          {usage.isPending ? <RowsSkeleton rows={4} /> : <GroupTable rows={usage.data?.by_job ?? []} keyName="job" label="Step" />}
        </section>
      </div>

      {(usage.data?.failures ?? []).length > 0 && (
        <section className="space-y-3">
          <SectionTitle>Why calls failed</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {(usage.data?.failures ?? []).map((failure) => (
              <Badge key={failure.kind} variant="critical" className="px-2 py-1">
                <AlertTriangle /> {humanize(failure.kind)}: {failure.count}
              </Badge>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <SectionTitle
          aside={
            routes.data ? (
              <span className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Gauge className="size-3.5" /> {routes.data.settings.user_daily_questions} questions per person per day
                </span>
                <span className="inline-flex items-center gap-1">
                  {routes.data.settings.cache_enabled ? <CheckCircle2 className="size-3.5 text-success" /> : <Timer className="size-3.5" />}
                  Cache {routes.data.settings.cache_enabled ? `on (${routes.data.settings.cache_ttl_hours} h)` : "off"}
                </span>
              </span>
            ) : undefined
          }
        >
          Model routes
        </SectionTitle>
        {routes.isPending ? (
          <RowsSkeleton rows={4} />
        ) : routes.isError ? (
          <ErrorState error={routes.error} onRetry={() => void routes.refetch()} />
        ) : (
          <div className="space-y-5">
            {tiers.map((tier) => (
              <div key={tier} className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {humanize(tier)} tasks <span className="normal-case tracking-normal">(tried in order)</span>
                </p>
                <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {(routes.data?.routes ?? [])
                    .filter((route) => route.tier === tier)
                    .sort((a, b) => a.position - b.position)
                    .map((route) => (
                      <RouteCard key={`${tier}-${route.route}`} route={route} />
                    ))}
                </ul>
              </div>
            ))}
            {(routes.data?.routes ?? []).every((route) => !route.key_configured) && (
              <p className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/[0.06] px-3 py-2 text-[13px] text-warning">
                <KeyRound className="size-4 shrink-0" /> No AI keys are set on the server. Add GEMINI_API_KEY, GROQ_API_KEY or OPENROUTER_API_KEY to the backend .env.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
