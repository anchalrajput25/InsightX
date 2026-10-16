"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ComposedChart, Line, PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, Gavel, ScanSearch, ShieldCheck } from "lucide-react";
import { axisProps, ChartTooltip, gridProps, Legend, shortDate } from "@/components/charts/chart-kit";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { AnswerList, AnswerSheet, findingLabel, pct, ScoreRing, Segmented } from "@/components/llmops/llmops-kit";
import { Input } from "@/components/ui/input";
import { llmopsApi, type AnswerFilter, type RateRow } from "@/lib/api/llmops";
import { formatNumber } from "@/lib/format";

const DAYS = [
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

const RISK_COLORS: Record<string, string> = { none: "var(--success)", low: "var(--info)", medium: "var(--warning)", high: "var(--critical)" };

function RateTable({ title, rows }: { title: string; rows: Record<string, RateRow> }) {
  const entries = Object.entries(rows);
  return (
    <div className="rounded-xl border bg-card p-4">
      <SectionTitle>{title}</SectionTitle>
      {entries.length === 0 ? (
        <p className="py-6 text-center text-xs text-muted-foreground">No data yet.</p>
      ) : (
        <ul className="space-y-2.5">
          {entries.map(([name, row]) => {
            const rate = row.checked ? row.hallucinated / row.checked : 0;
            return (
              <li key={name} className="grid grid-cols-[80px_1fr_70px] items-center gap-3 text-xs">
                <span className="capitalize text-muted-foreground">{name}</span>
                <span className="h-2 rounded-full bg-muted">
                  <span className="block h-2 rounded-full bg-critical/80 transition-[width] duration-700" style={{ width: `${Math.max(rate * 100, rate ? 2 : 0)}%` }} />
                </span>
                <span className="tabular text-right">
                  {pct(rate)} <span className="text-muted-foreground">of {row.checked}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function HallucinationScreen() {
  const [days, setDays] = React.useState(30);
  const [filter, setFilter] = React.useState<AnswerFilter>("flagged");
  const [search, setSearch] = React.useState("");
  const [open, setOpen] = React.useState<string | null>(null);
  const summary = useQuery({ queryKey: ["llmops", "hallucination", days], queryFn: () => llmopsApi.hallucination(days), refetchInterval: 20_000 });
  const list = useQuery({ queryKey: ["llmops", "answers", filter, search, days], queryFn: () => llmopsApi.answers(filter, search, days) });
  const s = summary.data;

  const kinds = Object.entries(s?.kinds ?? {}).map(([kind, count]) => ({ kind: findingLabel(kind), count }));
  const radar = Object.keys(s?.weights ?? {}).map((kind) => ({ kind: findingLabel(kind), count: s?.kinds?.[kind] ?? 0 }));
  const riskTotal = Object.values(s?.risk ?? {}).reduce((a, b) => a + (b ?? 0), 0);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="Hallucination"
        description="Every answer is checked against the rows it came from: numbers, names, rankings and counts. Open an answer to see what was flagged, or ask the AI judge for a second opinion."
        actions={<Segmented label="Period" value={days} options={DAYS} onChange={setDays} />}
      />

      {summary.isPending ? (
        <CardsSkeleton count={4} />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
            <SpotlightCard className="flex flex-col items-center justify-center gap-3 rounded-xl border bg-card p-6 text-center">
              <ScoreRing value={s?.mean_faithfulness} size={150} label="grounded" />
              <div>
                <p className="text-sm font-medium">Average grounding score</p>
                <p className="text-xs text-muted-foreground">1.0 means every claim was found in the result</p>
              </div>
              {riskTotal > 0 && (
                <div className="w-full space-y-1">
                  <div className="flex h-2 w-full overflow-hidden rounded-full">
                    {(["none", "low", "medium", "high"] as const).map((risk) => (
                      <span key={risk} style={{ width: `${((s?.risk?.[risk] ?? 0) / riskTotal) * 100}%`, background: RISK_COLORS[risk] }} title={`${risk}: ${s?.risk?.[risk] ?? 0}`} />
                    ))}
                  </div>
                  <Legend items={(["none", "low", "medium", "high"] as const).map((risk) => ({ label: `${risk === "none" ? "grounded" : risk} ${s?.risk?.[risk] ?? 0}`, color: RISK_COLORS[risk] as string }))} />
                </div>
              )}
            </SpotlightCard>
            <div className="fx-stagger grid grid-cols-2 gap-3 self-start">
              <StatCard label="Answers checked" value={formatNumber(s?.checked)} icon={ScanSearch} />
              <StatCard label="Hallucination rate" value={pct(s?.rate, 1)} icon={AlertTriangle} tone={(s?.rate ?? 0) > 0.05 ? "critical" : "success"} hint={`${formatNumber(s?.hallucinated)} answers`} />
              <StatCard label="Checked by AI judge" value={formatNumber(s?.judged)} icon={Gavel} tone="primary" hint="Runs only when you ask" />
              <StatCard label="Judge agrees with checks" value={pct(s?.judge_agreement)} icon={ShieldCheck} hint="How often both say the same" />
              <div className="col-span-2 rounded-xl border bg-card p-4">
                <SectionTitle>Rate over time</SectionTitle>
                <Legend items={[{ label: "Checked", color: "var(--chart-1)" }, { label: "Hallucination rate", color: "var(--critical)" }]} />
                <div className="mt-2 h-44">
                  {(s?.by_day ?? []).length === 0 ? (
                    <p className="pt-14 text-center text-xs text-muted-foreground">Ask questions to build this chart.</p>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={(s?.by_day ?? []).map((d) => ({ ...d, rate_pct: d.rate === null ? null : d.rate * 100 }))} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="day" tickFormatter={shortDate} minTickGap={16} />
                        <YAxis {...axisProps} yAxisId="n" width={28} allowDecimals={false} />
                        <YAxis {...axisProps} yAxisId="r" orientation="right" width={36} unit="%" />
                        <Tooltip content={<ChartTooltip labelFormatter={shortDate} />} />
                        <Bar yAxisId="n" dataKey="checked" name="Checked" fill="var(--chart-1)" opacity={0.55} maxBarSize={22} radius={[3, 3, 0, 0]} />
                        <Line yAxisId="r" type="monotone" dataKey="rate_pct" name="Rate %" stroke="var(--critical)" strokeWidth={2} dot={{ r: 2 }} connectNulls />
                      </ComposedChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="rounded-xl border bg-card p-4">
              <SectionTitle>What goes wrong</SectionTitle>
              {kinds.length === 0 ? (
                <p className="py-10 text-center text-xs text-muted-foreground">No findings yet.</p>
              ) : (
                <div className="h-60">
                  <ResponsiveContainer width="100%" height="100%">
                    <RadarChart data={radar} outerRadius="70%">
                      <PolarGrid stroke="var(--chart-grid)" />
                      <PolarAngleAxis dataKey="kind" tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} />
                      <Radar dataKey="count" name="Findings" stroke="var(--critical)" fill="var(--critical)" fillOpacity={0.25} />
                      <Tooltip content={<ChartTooltip />} />
                    </RadarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
            <RateTable title="By question difficulty" rows={s?.by_difficulty ?? {}} />
            <RateTable title="By trust level" rows={s?.by_trust ?? {}} />
          </div>

          {kinds.length > 0 && (
            <div className="rounded-xl border bg-card p-4">
              <SectionTitle>Findings count</SectionTitle>
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={kinds} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 0 }}>
                    <CartesianGrid {...gridProps} horizontal={false} vertical />
                    <XAxis {...axisProps} type="number" allowDecimals={false} />
                    <YAxis {...axisProps} type="category" dataKey="kind" width={170} />
                    <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.4 }} />
                    <Bar dataKey="count" name="Findings" fill="var(--warning)" radius={[0, 3, 3, 0]} maxBarSize={16} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </>
      )}

      <section className="space-y-3">
        <SectionTitle
          aside={
            <div className="flex flex-wrap items-center gap-2">
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search questions" className="h-8 w-48" />
              <Segmented
                label="Show"
                value={filter}
                onChange={setFilter}
                options={[
                  { value: "flagged", label: "Flagged" },
                  { value: "hallucinated", label: "Hallucinated" },
                  { value: "all", label: "All" },
                ]}
              />
            </div>
          }
        >
          Answers
        </SectionTitle>
        {list.isPending ? (
          <RowsSkeleton rows={5} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : (
          <AnswerList
            rows={list.data.answers}
            onOpen={setOpen}
            empty={<EmptyState compact icon={ShieldCheck} title={filter === "all" ? "No answers yet" : "Nothing flagged"} description="Answers from the Ask page are checked automatically and appear here." />}
          />
        )}
        <p className="text-[11px] text-muted-foreground">
          The score on the right is the grounding score. Each finding lowers it by its weight, for example a number that is not in the result lowers it by {Math.round((s?.weights?.unverified_number ?? 0.3) * 100)} points.
        </p>
      </section>

      <AnswerSheet answerId={open} onClose={() => setOpen(null)} />
    </div>
  );
}
