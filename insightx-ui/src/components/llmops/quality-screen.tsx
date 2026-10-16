"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Area, Bar, CartesianGrid, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowRight, CheckCircle2, FlaskConical, Inbox, Lightbulb, MessageSquareWarning, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import { axisProps, ChartTooltip, gridProps, Legend, shortDate } from "@/components/charts/chart-kit";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { AnswerList, AnswerSheet, findingLabel, pct, Segmented } from "@/components/llmops/llmops-kit";
import { Input } from "@/components/ui/input";
import { llmopsApi, type AnswerFilter } from "@/lib/api/llmops";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

const DAYS = [
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

const LOOP = [
  { icon: MessageSquareWarning, title: "Catch", text: "Thumbs down, low trust, errors and flagged answers land in the review queue." },
  { icon: Sparkles, title: "Diagnose", text: "Open an answer: grounding checks, AI judge and the saved rows show what went wrong." },
  { icon: CheckCircle2, title: "Fix", text: "Correct the SQL and test it on your database before saving anything." },
  { icon: FlaskConical, title: "Lock in", text: "Promote it to a golden test. Every later evaluation run checks it, so it cannot silently break again." },
];

export function QualityScreen() {
  const [days, setDays] = React.useState(30);
  const [filter, setFilter] = React.useState<AnswerFilter>("review");
  const [search, setSearch] = React.useState("");
  const [open, setOpen] = React.useState<string | null>(null);
  const summary = useQuery({ queryKey: ["llmops", "quality", days], queryFn: () => llmopsApi.quality(days), refetchInterval: 20_000 });
  const list = useQuery({ queryKey: ["llmops", "answers", filter, search, days], queryFn: () => llmopsApi.answers(filter, search, days) });
  const q = summary.data;
  const states = q?.review_states ?? {};

  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="Quality"
        description="The feedback loop: rate answers, review the weak ones, fix them, and turn each fix into a test that guards future changes."
        actions={<Segmented label="Period" value={days} options={DAYS} onChange={setDays} />}
      />

      <ol className="fx-stagger grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {LOOP.map((step, index) => (
          <li key={step.title} className="relative">
            <SpotlightCard className="h-full rounded-xl border bg-card p-4">
              <div className="flex items-center gap-2">
                <span className="flex size-7 items-center justify-center rounded-md bg-primary/12 text-primary">
                  <step.icon className="size-3.5" />
                </span>
                <span className="text-[11px] text-muted-foreground">Step {index + 1}</span>
              </div>
              <p className="mt-2 text-sm font-medium">{step.title}</p>
              <p className="mt-1 text-xs text-muted-foreground">{step.text}</p>
            </SpotlightCard>
            {index < LOOP.length - 1 && <ArrowRight className="absolute top-1/2 -right-3 z-10 hidden size-4 -translate-y-1/2 text-muted-foreground lg:block" />}
          </li>
        ))}
      </ol>

      {summary.isPending ? (
        <CardsSkeleton count={4} />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <>
          <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Answers" value={formatNumber(q?.answers)} icon={Inbox} hint={`${pct(q?.success_rate)} without error`} />
            <StatCard label="Satisfaction" value={pct(q?.satisfaction)} icon={ThumbsUp} tone="success" hint={`${q?.up ?? 0} good, ${q?.down ?? 0} bad`} />
            <StatCard label="Waiting for review" value={formatNumber(q?.review_queue.length)} icon={MessageSquareWarning} tone={(q?.review_queue.length ?? 0) > 0 ? "warning" : "default"} hint={`${states.fixed ?? 0} fixed, ${states.dismissed ?? 0} dismissed`} />
            <StatCard label="Golden tests added" value={formatNumber(q?.promoted)} icon={FlaskConical} tone="primary" hint={<Link href="/evals" className="text-primary hover:underline">See them on Evals</Link>} />
          </div>

          <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
            <div className="rounded-xl border bg-card p-4">
              <SectionTitle aside={<span className="text-[11px] text-muted-foreground">quality index 0-100: success, trust and feedback</span>}>Quality over time</SectionTitle>
              <Legend items={[{ label: "Quality index", color: "var(--chart-2)" }, { label: "Good", color: "var(--success)" }, { label: "Bad", color: "var(--critical)" }]} />
              <div className="mt-2 h-60">
                {(q?.by_day ?? []).length === 0 ? (
                  <p className="pt-20 text-center text-xs text-muted-foreground">Ask questions and rate answers to build this chart.</p>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={q?.by_day ?? []} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                      <defs>
                        <linearGradient id="q-index" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid {...gridProps} />
                      <XAxis {...axisProps} dataKey="day" tickFormatter={shortDate} minTickGap={16} />
                      <YAxis {...axisProps} yAxisId="q" width={32} domain={[0, 100]} />
                      <YAxis {...axisProps} yAxisId="n" orientation="right" width={28} allowDecimals={false} />
                      <Tooltip content={<ChartTooltip labelFormatter={shortDate} />} />
                      <Area yAxisId="q" type="monotone" dataKey="quality" name="Quality index" stroke="var(--chart-2)" strokeWidth={2} fill="url(#q-index)" dot={{ r: 3, fill: "var(--chart-2)" }} connectNulls />
                      <Bar yAxisId="n" dataKey="up" name="Good" stackId="r" fill="var(--success)" maxBarSize={14} />
                      <Bar yAxisId="n" dataKey="down" name="Bad" stackId="r" fill="var(--critical)" maxBarSize={14} radius={[3, 3, 0, 0]} />
                    </ComposedChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
            <div className="rounded-xl border bg-card p-4">
              <SectionTitle>What to improve</SectionTitle>
              {(q?.suggestions ?? []).length === 0 ? (
                <EmptyState compact icon={Lightbulb} title="Nothing stands out" description="Suggestions appear when the checks find the same kind of problem more than once." className="border-0 bg-transparent" />
              ) : (
                <ul className="space-y-3">
                  {q!.suggestions.map((tip) => (
                    <li key={tip.kind} className="flex gap-3">
                      <Lightbulb className="mt-0.5 size-4 shrink-0 text-warning" />
                      <div>
                        <p className="text-xs font-medium">
                          {findingLabel(tip.kind)} <span className="text-muted-foreground">({tip.count})</span>
                        </p>
                        <p className="text-xs text-muted-foreground">{tip.text}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
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
                  { value: "review", label: "To review" },
                  { value: "down", label: "Bad" },
                  { value: "up", label: "Good" },
                  { value: "all", label: "All" },
                ]}
              />
            </div>
          }
        >
          {filter === "review" ? "Review queue" : "Answers"}
        </SectionTitle>
        {list.isPending ? (
          <RowsSkeleton rows={5} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : (
          <AnswerList
            rows={list.data.answers}
            onOpen={setOpen}
            empty={
              <EmptyState
                compact
                icon={filter === "review" ? CheckCircle2 : filter === "down" ? ThumbsDown : Inbox}
                title={filter === "review" ? "Review queue is empty" : "No answers here"}
                description="Ask questions on the Ask page. Each answer is saved here so you can rate it."
              />
            }
          />
        )}
        <p className={cn("text-[11px] text-muted-foreground")}>An answer joins the review queue when you rate it bad, its trust is low, it failed, or its grounding risk is medium or high.</p>
      </section>

      <AnswerSheet answerId={open} onClose={() => setOpen(null)} />
    </div>
  );
}
