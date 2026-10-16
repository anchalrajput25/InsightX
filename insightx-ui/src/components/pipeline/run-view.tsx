"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Activity, Brain, Check, ChevronDown, Clock, Copy, Database, Layers, Loader2, MessageSquareText, MinusCircle, XCircle } from "lucide-react";
import { TrustBadge } from "@/components/ask/trust-badge";
import { STAGES, type StageDef } from "@/components/pipeline/stages";
import type { PipelineResult, Run } from "@/components/pipeline/pipeline-types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { monitoringApi } from "@/lib/api/monitoring";
import { formatNumber, formatSeconds, humanize } from "@/lib/format";
import type { TraceEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

type Status = "done" | "error" | "cache" | "skipped" | "waiting";

interface StageState {
  def: StageDef;
  status: Status;
  seconds: number | null;
  start: number | null;
  aiCalls: number;
}

const STATUS = {
  done: { label: "Done", dot: "bg-success", ring: "border-success/40 text-success", icon: Check },
  cache: { label: "Reused", dot: "bg-info", ring: "border-info/40 text-info", icon: Check },
  error: { label: "Failed", dot: "bg-critical", ring: "border-critical/50 text-critical", icon: XCircle },
  skipped: { label: "Skipped", dot: "bg-muted-foreground/40", ring: "border-border text-muted-foreground", icon: MinusCircle },
  waiting: { label: "Waiting", dot: "bg-muted-foreground/40", ring: "border-border text-muted-foreground", icon: Loader2 },
} as const;

function stageStates(result: PipelineResult, events: TraceEvent[]): StageState[] {
  const steps = events.filter((event) => event.kind === "step");
  const calls = events.filter((event) => event.kind === "llm");
  return STAGES.map((def) => {
    const own = steps.filter((event) => def.steps.includes(event.name));
    const seconds = own.length ? own.reduce((sum, event) => sum + (event.seconds || 0), 0) : null;
    const start = own.length ? Math.min(...own.map((event) => event.start || 0)) : null;
    const aiCalls = calls.filter((call) => own.some((event) => call.start >= event.start - 0.001 && call.start <= event.start + event.seconds + 0.001)).length;
    let status: Status = "skipped";
    if (own.some((event) => event.status === "error") || (result.error_stage && def.steps.includes(result.error_stage)) || def.failed?.(result)) status = "error";
    else if (own.some((event) => event.status === "cache")) status = own.every((event) => event.status === "cache") && !def.ran(result) ? "skipped" : "cache";
    else if (own.length > 0 || def.ran(result)) status = "done";
    if (def.key === "cache" && own.some((event) => event.status === "cache")) status = "cache";
    return { def, status, seconds, start, aiCalls };
  });
}

function useTraceEvents(traceId: string | undefined) {
  const query = useQuery({
    queryKey: ["trace", traceId],
    queryFn: () => monitoringApi.trace(traceId as string),
    enabled: !!traceId,
    staleTime: Infinity,
    retry: false,
  });
  return { events: query.data?.detail?.events ?? [], loading: query.isLoading && !!traceId, failed: query.isError };
}

// ---------------------------------------------------------------- flow map

function FlowMap({ states, running, onPick }: { states: StageState[]; running?: boolean; onPick?: (key: string) => void }) {
  return (
    <div>
      <ol className="flex flex-wrap items-start gap-y-1">
        {states.map((state, index) => {
          const meta = STATUS[state.status];
          const Icon = state.def.icon;
          return (
            <li key={state.def.key} className="flex items-center">
              <button
                type="button"
                onClick={() => onPick?.(state.def.key)}
                disabled={running}
                className={cn(
                  "group flex w-[84px] cursor-pointer flex-col items-center gap-1.5 rounded-lg px-1 py-1.5 text-center transition-colors hover:bg-accent/60 disabled:cursor-default",
                  running && "fx-enter",
                )}
                style={running ? { ["--d" as string]: `${index * 60}ms` } : undefined}
              >
                <span
                  className={cn(
                    "relative flex size-9 items-center justify-center rounded-xl border bg-card fx-surface transition-shadow",
                    meta.ring,
                    state.status === "done" && "shadow-[0_0_18px_-6px_var(--success)]",
                    state.status === "error" && "shadow-[0_0_18px_-4px_var(--critical)]",
                    running && "animate-pulse",
                  )}
                >
                  <Icon className="size-4" />
                  <span className={cn("absolute -right-0.5 -top-0.5 size-2 rounded-full ring-2 ring-card", meta.dot)} />
                </span>
                <span className="line-clamp-2 text-[10.5px] leading-tight font-medium text-muted-foreground group-hover:text-foreground">{state.def.title}</span>
              </button>
              {index < states.length - 1 && (
                <span
                  aria-hidden
                  className={cn(
                    "mt-[22px] h-px w-3 shrink-0",
                    running ? "bg-[linear-gradient(90deg,var(--primary),transparent)] opacity-60" : state.status === "skipped" ? "bg-border" : "bg-[linear-gradient(90deg,var(--success),var(--border))]",
                  )}
                />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- timing

function Timing({ states, events, total }: { states: StageState[]; events: TraceEvent[]; total: number }) {
  const mapped = new Set(states.flatMap((state) => state.def.steps));
  const extra = events
    .filter((event) => event.kind === "step" && !mapped.has(event.name))
    .map((event) => ({ key: `extra-${event.name}-${event.start}`, title: humanize(event.name), start: event.start || 0, seconds: event.seconds || 0, status: event.status === "error" ? "error" : "extra" }));
  const timed = [
    ...states
      .filter((state) => state.seconds !== null && state.start !== null)
      .map((state) => ({ key: state.def.key, title: state.def.title, start: state.start ?? 0, seconds: state.seconds ?? 0, status: state.status as string })),
    ...extra,
  ].sort((a, b) => a.start - b.start);
  if (timed.length === 0) return null;
  const end = Math.max(total, ...events.map((event) => (event.start || 0) + (event.seconds || 0)), 0.001);
  const calls = events.filter((event) => event.kind === "llm");
  const queries = events.filter((event) => event.kind === "db");
  return (
    <div className="space-y-2">
      {timed.map((state) => {
        const left = (state.start / end) * 100;
        const width = Math.max((state.seconds / end) * 100, 0.6);
        return (
          <div key={state.key} className="grid grid-cols-[minmax(0,9rem)_1fr_3.5rem] items-center gap-3 text-xs">
            <span className={cn("truncate", state.status === "extra" ? "text-muted-foreground/70" : "text-muted-foreground")}>{state.title}</span>
            <span className="relative h-2.5 rounded-full bg-muted/60">
              <span
                className={cn("absolute inset-y-0 rounded-full", state.status === "error" ? "bg-critical" : state.status === "cache" ? "bg-info" : state.status === "extra" ? "bg-muted-foreground/40" : "bg-[linear-gradient(90deg,var(--chart-1),var(--chart-2))]")}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </span>
            <span className="tabular text-right text-muted-foreground">{formatSeconds(state.seconds)}</span>
          </div>
        );
      })}
      <p className="pt-1 text-[11px] text-muted-foreground">
        {calls.length} AI {calls.length === 1 ? "call" : "calls"} and {queries.length} database {queries.length === 1 ? "query" : "queries"} in this run.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- stage list

function StageItem({ state, index, open, onToggle, result }: { state: StageState; index: number; open: boolean; onToggle: () => void; result: PipelineResult }) {
  const meta = STATUS[state.status];
  const Icon = state.def.icon;
  const Body = state.def.Body;
  const skipped = state.status === "skipped";
  return (
    <li id={`stage-${state.def.key}`} className="relative scroll-mt-24 pl-12">
      <span aria-hidden className="absolute left-[19px] top-10 bottom-[-12px] w-px bg-border" />
      <span className={cn("absolute left-0 top-1.5 flex size-10 items-center justify-center rounded-xl border bg-card fx-surface", meta.ring)}>
        <Icon className="size-4" />
      </span>
      <div className={cn("rounded-xl border bg-card fx-surface transition-colors", open && "border-foreground/12", state.status === "error" && "border-critical/40")}>
        <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full cursor-pointer items-start gap-3 p-4 text-left">
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="tabular text-xs text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
              <span className="text-sm font-semibold">{state.def.title}</span>
              <Badge variant={state.status === "error" ? "critical" : state.status === "done" ? "success" : state.status === "cache" ? "info" : "outline"}>{meta.label}</Badge>
              {state.seconds !== null && (
                <Badge variant="outline">
                  <Clock /> {formatSeconds(state.seconds)}
                </Badge>
              )}
              {state.aiCalls > 0 && (
                <Badge variant="outline">
                  <Brain /> {state.aiCalls} AI {state.aiCalls === 1 ? "call" : "calls"}
                </Badge>
              )}
            </span>
            <span className={cn("mt-1 block truncate text-[13px]", skipped ? "text-muted-foreground" : "text-foreground/85")}>{skipped ? "Not needed for this question." : state.def.summary(result)}</span>
          </span>
          <ChevronDown className={cn("mt-1 size-4 shrink-0 text-muted-foreground transition-transform duration-200", open && "rotate-180")} />
        </button>
        {open && (
          <div className="fx-enter border-t px-4 pt-3 pb-4">
            <p className="mb-3 text-xs text-muted-foreground">{state.def.about}</p>
            <Body result={result} />
          </div>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------- running

export function RunningView({ question, startedAt }: { question: string; startedAt: number }) {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  const waiting = STAGES.map((def) => ({ def, status: "waiting" as const, seconds: null, start: null, aiCalls: 0 }));
  return (
    <div className="space-y-5" aria-busy>
      <div className="fx-beam fx-surface rounded-2xl border bg-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-primary">Running</p>
            <p className="mt-1 truncate text-base font-semibold">{question}</p>
          </div>
          <span className="tabular flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin text-primary" /> {((now - startedAt) / 1000).toFixed(1)} s
          </span>
        </div>
        <div className="mt-4">
          <FlowMap states={waiting} running />
        </div>
        <p className="mt-3 text-xs text-muted-foreground">The backend sends all steps together when the run finishes. Each step will then show what it did and how long it took.</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- run view

export function RunView({ run }: { run: Extract<Run, { state: "done" }> }) {
  const result = run.result;
  const { events, loading, failed } = useTraceEvents(result.trace?.id);
  const states = React.useMemo(() => stageStates(result, events), [result, events]);
  const [open, setOpen] = React.useState<Set<string>>(() => new Set(["rewrite", "sql"]));
  const [copied, setCopied] = React.useState(false);

  const toggle = (key: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const jump = (key: string) => {
    setOpen((current) => new Set(current).add(key));
    window.requestAnimationFrame(() => document.getElementById(`stage-${key}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const ok = String(result.status).toUpperCase() === "SUCCESS";
  const ranCount = states.filter((state) => state.status !== "skipped").length;
  const trace = result.trace ?? {};

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(result, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="fx-enter fx-surface relative isolate overflow-hidden rounded-2xl border bg-card p-5">
        <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 -z-10 size-72 rounded-full bg-[var(--glow)] blur-3xl" />
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={ok ? "success" : String(result.status).toUpperCase() === "CLARIFICATION" ? "warning" : "critical"}>{humanize(String(result.status || "unknown").toLowerCase())}</Badge>
              <TrustBadge trust={result.trust} />
              <Badge variant="outline">{run.source === "ask" ? "From an Ask chat" : "Run on this page"}</Badge>
            </div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-balance">{result.question || run.question}</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {ranCount} of {states.length} steps ran
              {result.error_stage ? `, stopped at ${humanize(result.error_stage)}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href={`/ask?q=${encodeURIComponent(run.question)}&mode=ask`}>
                <MessageSquareText /> Ask in chat
              </Link>
            </Button>
            {trace.id && (
              <Button variant="outline" size="sm" asChild>
                <Link href={`/traces?id=${encodeURIComponent(trace.id)}`}>
                  <Activity /> Full trace
                </Link>
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={copy}>
              {copied ? <Check className="text-success" /> : <Copy />} {copied ? "Copied" : "Copy as JSON"}
            </Button>
          </div>
        </div>

        <dl className="fx-stagger mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            { icon: Clock, label: "Total time", value: formatSeconds(trace.seconds ?? null) },
            { icon: Brain, label: "AI calls", value: trace.llm_calls !== undefined ? formatNumber(trace.llm_calls) : "-" },
            { icon: Layers, label: "Tokens", value: trace.tokens !== undefined ? formatNumber(trace.tokens) : "-" },
            { icon: Database, label: "Database queries", value: trace.db_queries !== undefined ? formatNumber(trace.db_queries) : "-" },
          ].map((item) => (
            <div key={item.label} className="rounded-lg border bg-background/40 px-3 py-2">
              <dt className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <item.icon className="size-3.5" /> {item.label}
              </dt>
              <dd className="tabular mt-0.5 text-base font-semibold">{item.value}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-5">
          <FlowMap states={states} onPick={jump} />
        </div>
      </div>

      {result.error && (
        <p role="alert" className="rounded-xl border border-critical/30 bg-critical/10 px-4 py-3 text-[13px] text-critical">
          {result.error}
        </p>
      )}

      {(events.length > 0 || loading) && (
        <section className="fx-enter fx-surface rounded-2xl border bg-card p-5">
          <h3 className="mb-3 text-sm font-semibold">Where the time went</h3>
          {loading ? <p className="text-xs text-muted-foreground">Loading timings…</p> : <Timing states={states} events={events} total={trace.seconds ?? 0} />}
        </section>
      )}
      {failed && <p className="text-xs text-muted-foreground">Step timings are not available for this run.</p>}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Every step</h3>
          <div className="flex gap-1">
            <Button variant="ghost" size="xs" onClick={() => setOpen(new Set(STAGES.map((stage) => stage.key)))}>
              Open all
            </Button>
            <Button variant="ghost" size="xs" onClick={() => setOpen(new Set())}>
              Close all
            </Button>
          </div>
        </div>
        <ol className="space-y-3">
          {states.map((state, index) => (
            <StageItem key={state.def.key} state={state} index={index} open={open.has(state.def.key)} onToggle={() => toggle(state.def.key)} result={result} />
          ))}
        </ol>
      </section>
    </div>
  );
}
