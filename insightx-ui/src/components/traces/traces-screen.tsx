"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Brain, Clock, Compass, Cpu, Database, Layers, MessageSquareText, X } from "lucide-react";
import { CodeBlock } from "@/components/common/code-block";
import { PageHeader } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { monitoringApi } from "@/lib/api/monitoring";
import { formatDateTime, formatNumber, formatSeconds, humanize, stripEmoji, timeAgo } from "@/lib/format";
import type { TraceEvent, TraceListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const KIND = {
  step: { label: "Step", icon: Layers, bar: "bg-chart-1", dot: "bg-chart-1" },
  llm: { label: "AI call", icon: Brain, bar: "bg-chart-4", dot: "bg-chart-4" },
  db: { label: "Database", icon: Database, bar: "bg-chart-2", dot: "bg-chart-2" },
} as const;

function kindOf(event: TraceEvent) {
  return KIND[event.kind as keyof typeof KIND] ?? { label: humanize(event.kind), icon: Activity, bar: "bg-muted-foreground", dot: "bg-muted-foreground" };
}

function statusBadge(status: string) {
  const upper = status.toUpperCase();
  if (upper === "SUCCESS") return <Badge variant="success">Success</Badge>;
  if (upper === "CLARIFICATION") return <Badge variant="info">Asked back</Badge>;
  if (upper === "ERROR" || upper === "FAILED") return <Badge variant="critical">Failed</Badge>;
  return <Badge variant="outline">{status || "Unknown"}</Badge>;
}

function Waterfall({ events, total, selected, onSelect }: { events: TraceEvent[]; total: number; selected: number | null; onSelect: (index: number) => void }) {
  const span = Math.max(total, ...events.map((event) => event.start + event.seconds), 0.001);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className="space-y-1">
      <div className="relative ml-[38%] h-5 border-b text-[10px] text-muted-foreground sm:ml-[30%]">
        {ticks.map((tick) => (
          <span key={tick} className={cn("tabular absolute whitespace-nowrap", tick === 0 ? "" : tick === 1 ? "-translate-x-full" : "-translate-x-1/2")} style={{ left: `${tick * 100}%` }}>
            {formatSeconds(span * tick)}
          </span>
        ))}
      </div>
      <ul className="space-y-px">
        {events.map((event, index) => {
          const kind = kindOf(event);
          const left = (event.start / span) * 100;
          const width = Math.max((event.seconds / span) * 100, 0.4);
          const failed = event.status && event.status !== "ok";
          return (
            <li key={index}>
              <button
                type="button"
                onClick={() => onSelect(index)}
                aria-pressed={selected === index}
                className={cn("flex w-full items-center gap-2 rounded-md py-1 pr-2 text-left transition-colors hover:bg-accent/70", selected === index && "bg-accent")}
              >
                <span className="flex w-[38%] min-w-0 items-center gap-2 pl-2 sm:w-[30%]">
                  <span className={cn("size-2 shrink-0 rounded-full", kind.dot)} />
                  <span className="truncate text-xs">
                    {event.kind === "llm" ? event.model || event.route || humanize(event.name) : humanize(event.name)}
                  </span>
                  {failed && <AlertTriangle className="size-3 shrink-0 text-critical" />}
                </span>
                <span className="relative h-4 flex-1">
                  {ticks.slice(1, -1).map((tick) => (
                    <span key={tick} aria-hidden className="absolute inset-y-0 w-px bg-border/60" style={{ left: `${tick * 100}%` }} />
                  ))}
                  <span
                    className={cn("absolute inset-y-0.5 rounded-sm", failed ? "bg-critical/80" : kind.bar)}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  />
                </span>
                <span className="tabular w-14 shrink-0 text-right text-[11px] text-muted-foreground">{formatSeconds(event.seconds)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EventDetail({ event }: { event: TraceEvent }) {
  const kind = kindOf(event);
  const rows: [string, React.ReactNode][] = [
    ["Type", kind.label],
    ["Started at", formatSeconds(event.start)],
    ["Took", formatSeconds(event.seconds)],
    ["Status", event.status ?? "ok"],
  ];
  if (event.model) rows.push(["Model", event.model]);
  if (event.route && event.route !== event.model) rows.push(["Route", event.route]);
  if (typeof event.prompt_tokens === "number") rows.push(["Tokens", `${formatNumber(event.prompt_tokens)} in, ${formatNumber(event.completion_tokens ?? 0)} out`]);
  if (typeof event.rows === "number") rows.push(["Rows", formatNumber(event.rows)]);
  return (
    <div className="space-y-3 rounded-lg border bg-surface/60 p-4">
      <p className="text-sm font-medium">{humanize(event.name)}</p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="mt-0.5 font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      {event.error && <p className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-xs text-critical">{String(event.error)}</p>}
      {event.sql && <CodeBlock code={event.sql} />}
    </div>
  );
}

function TraceDetailPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const trace = useQuery({ queryKey: ["traces", "detail", id], queryFn: () => monitoringApi.trace(id) });
  const [selected, setSelected] = React.useState<number | null>(null);
  React.useEffect(() => setSelected(null), [id]);

  if (trace.isPending) {
    return (
      <div className="space-y-3 p-5">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-20" />
        <Skeleton className="h-48" />
      </div>
    );
  }
  if (trace.isError) return <ErrorState error={trace.error} onRetry={() => void trace.refetch()} className="m-5" />;
  const data = trace.data;
  const events = [...(data.detail.events ?? [])].sort((a, b) => a.start - b.start);
  const counts = { step: 0, llm: 0, db: 0 } as Record<string, number>;
  for (const event of events) counts[event.kind] = (counts[event.kind] ?? 0) + 1;
  const picked = selected !== null ? events[selected] : undefined;

  return (
    <div className="space-y-5 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {statusBadge(data.status)}
            <Badge variant="outline">{data.kind === "investigation" ? "Investigation" : "Question"}</Badge>
            <span className="text-xs text-muted-foreground">{formatDateTime(data.ts)}</span>
          </div>
          <h2 className="mt-2 text-[15px] font-medium leading-snug">{stripEmoji(data.question) || "(no question text)"}</h2>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close trace" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Total time", formatSeconds(data.seconds), Clock],
          ["AI calls", data.llm_calls, Cpu],
          ["Tokens", formatNumber(data.tokens), Brain],
          ["DB queries", data.db_queries, Database],
        ].map(([label, value, Icon]) => {
          const I = Icon as typeof Clock;
          return (
            <div key={label as string} className="rounded-lg border px-3 py-2">
              <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <I className="size-3" /> {label as string}
              </p>
              <p className="tabular mt-0.5 text-sm font-semibold">{value as React.ReactNode}</p>
            </div>
          );
        })}
      </div>
      {data.error && <p className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-xs text-critical">{data.error}</p>}
      {events.length === 0 ? (
        <p className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">No steps were recorded for this run.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            {(Object.keys(KIND) as (keyof typeof KIND)[]).map((key) => (
              <span key={key} className="inline-flex items-center gap-1.5">
                <span className={cn("size-2 rounded-full", KIND[key].dot)} /> {KIND[key].label} ({counts[key] ?? 0})
              </span>
            ))}
            <span className="ml-auto">Click a row for details</span>
          </div>
          <Waterfall events={events} total={data.seconds} selected={selected} onSelect={setSelected} />
          {picked && <EventDetail event={picked} />}
        </div>
      )}
    </div>
  );
}

function TraceRow({ trace, active, onOpen }: { trace: TraceListItem; active: boolean; onOpen: () => void }) {
  const Icon = trace.kind === "investigation" ? Compass : MessageSquareText;
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        aria-current={active ? "true" : undefined}
        className={cn("flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/60", active && "bg-accent")}
      >
        <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium">{stripEmoji(trace.question) || "(no question text)"}</span>
          <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {statusBadge(trace.status)}
            <span>{timeAgo(trace.ts)}</span>
            <span className="tabular">{formatSeconds(trace.seconds)}</span>
            <span className="tabular">{trace.llm_calls} AI</span>
            <span className="tabular">{trace.db_queries} DB</span>
            {trace.cache && <span>cache {trace.cache}</span>}
          </span>
        </span>
      </button>
    </li>
  );
}

export function TracesScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const selected = params.get("id");
  const traces = useQuery({ queryKey: ["traces", "list"], queryFn: () => monitoringApi.traces(100), refetchInterval: 30_000 });
  const list = traces.data?.traces ?? [];
  const open = (id: string | null) => router.replace(id ? `${pathname}?id=${encodeURIComponent(id)}` : pathname, { scroll: false });

  const finished = list.filter((trace) => trace.seconds > 0);
  const p50 = finished.length ? [...finished].sort((a, b) => a.seconds - b.seconds)[Math.floor(finished.length / 2)]?.seconds : null;
  const failed = list.filter((trace) => /error|fail/i.test(trace.status)).length;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8 sm:px-8">
      <PageHeader title="Traces" description="Every run, step by step: how long each part took, which AI model answered, and which queries ran." />
      {traces.isPending ? null : traces.isError ? null : (
        <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Runs shown" value={list.length} icon={Activity} />
          <StatCard label="Typical time" value={formatSeconds(p50 ?? null)} icon={Clock} hint="Median of these runs" />
          <StatCard label="AI calls" value={formatNumber(list.reduce((sum, trace) => sum + trace.llm_calls, 0))} icon={Cpu} />
          <StatCard label="Failed" value={failed} icon={AlertTriangle} tone={failed ? "critical" : "default"} />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,380px)_1fr]">
        <div className={cn("overflow-hidden rounded-xl border bg-card", selected && "hidden lg:block")}>
          {traces.isPending ? (
            <div className="p-4">
              <RowsSkeleton rows={8} />
            </div>
          ) : traces.isError ? (
            <ErrorState error={traces.error} onRetry={() => void traces.refetch()} className="m-4" />
          ) : list.length === 0 ? (
            <EmptyState compact icon={Activity} title="No runs yet" description="Ask a question and its trace appears here." className="py-14" />
          ) : (
            <ul className="scrollbar-thin max-h-[70dvh] divide-y overflow-y-auto">
              {list.map((trace) => (
                <TraceRow key={trace.id} trace={trace} active={trace.id === selected} onOpen={() => open(trace.id)} />
              ))}
            </ul>
          )}
        </div>
        <div className={cn("min-h-80 rounded-xl border bg-card", !selected && "hidden lg:block")}>
          {selected ? (
            <TraceDetailPanel id={selected} onClose={() => open(null)} />
          ) : (
            <EmptyState compact icon={Layers} title="Pick a run" description="Its waterfall timeline shows here." className="h-full py-20" />
          )}
        </div>
      </div>
    </div>
  );
}
