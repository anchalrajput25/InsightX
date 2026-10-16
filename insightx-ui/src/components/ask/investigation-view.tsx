"use client";

import * as React from "react";
import Link from "next/link";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  Clock,
  Code2,
  Compass,
  Database,
  Lightbulb,
  ListChecks,
  Microscope,
  Sparkles,
  Target,
} from "lucide-react";
import { axisProps, ChartTooltip, gridProps, shortDate } from "@/components/charts/chart-kit";
import { CodeBlock } from "@/components/common/code-block";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FollowUps, RichText } from "@/components/ask/answer-card";
import { TrustBadge } from "@/components/ask/trust-badge";
import { formatNumber, formatPercent, formatSeconds, humanize, stripEmoji } from "@/lib/format";
import type { Driver, InvestigationReport, Reason } from "@/lib/types";
import { cn } from "@/lib/utils";

function Panel({ icon: Icon, title, aside, children, className }: { icon: typeof Target; title: string; aside?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-xl border bg-card", className)}>
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <h3 className="flex items-center gap-2 text-[13px] font-medium">
          <Icon className="size-4 text-muted-foreground" /> {title}
        </h3>
        {aside}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

function changeTone(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) return "text-muted-foreground";
  return value < 0 ? "text-critical" : "text-success";
}

function Headline({ report }: { report: InvestigationReport }) {
  const h = report.headline;
  const down = (h.change ?? 0) < 0;
  const Arrow = down ? ArrowDownRight : ArrowUpRight;
  return (
    <div className="grid gap-px overflow-hidden rounded-xl border bg-border sm:grid-cols-[1.4fr_1fr_1fr]">
      <div className="bg-card p-4">
        <p className="text-xs text-muted-foreground">{humanize(h.metric)}</p>
        <div className="mt-1 flex items-baseline gap-2">
          <span className={cn("tabular text-3xl font-semibold tracking-tight", changeTone(h.change))}>{formatPercent(h.change_pct)}</span>
          <Arrow className={cn("size-5", changeTone(h.change))} />
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {h.unusual && (
            <Badge variant={h.unusual === "unusual" ? "warning" : "outline"}>{h.unusual === "unusual" ? "Unusual change" : humanize(h.unusual)}</Badge>
          )}
          {typeof h.typical_change_pct === "number" && <Badge variant="outline">Typical move {formatPercent(h.typical_change_pct)}</Badge>}
        </div>
      </div>
      <div className="bg-card p-4">
        <p className="text-xs text-muted-foreground">Before · {h.baseline_label}</p>
        <p className="tabular mt-1 text-xl font-semibold">{formatNumber(h.baseline, { compactFrom: 1e7 })}</p>
      </div>
      <div className="bg-card p-4">
        <p className="text-xs text-muted-foreground">After · {h.current_label}</p>
        <p className="tabular mt-1 text-xl font-semibold">{formatNumber(h.current, { compactFrom: 1e7 })}</p>
        <p className={cn("tabular mt-0.5 text-xs", changeTone(h.change))}>
          {h.change > 0 ? "+" : ""}
          {formatNumber(h.change)}
        </p>
      </div>
    </div>
  );
}

function Trend({ report }: { report: InvestigationReport }) {
  const data = report.trend.map((point) => ({ period: point.period, value: point.value, partial: point.partial }));
  if (data.length < 2) return null;
  const h = report.headline;
  const before = h.baseline_period ?? data[data.length - 2]?.period;
  const after = h.current_period ?? data[data.length - 1]?.period;
  return (
    <Panel
      icon={Activity}
      title={`${humanize(h.metric)} over time`}
      aside={<span className="text-xs text-muted-foreground">{h.period_choice ? stripEmoji(h.period_choice) : `per ${report.plan.grain}`}</span>}
    >
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="inv-trend" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.3} />
                <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid {...gridProps} />
            <XAxis {...axisProps} dataKey="period" tickFormatter={shortDate} minTickGap={20} />
            <YAxis {...axisProps} width={48} tickFormatter={(v) => formatNumber(v)} />
            <Tooltip content={<ChartTooltip labelFormatter={shortDate} />} />
            {before && after && <ReferenceArea x1={before} x2={after} fill="var(--warning)" fillOpacity={0.08} stroke="var(--warning)" strokeOpacity={0.25} strokeDasharray="3 3" />}
            <Area type="monotone" dataKey="value" name={humanize(h.metric)} stroke="var(--chart-1)" strokeWidth={1.8} fill="url(#inv-trend)" dot={data.length <= 24 ? { r: 2.5 } : false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">The shaded part is the change being explained.</p>
    </Panel>
  );
}

function VolumeValue({ report }: { report: InvestigationReport }) {
  const v = report.volume_value;
  if (!v || v.records_baseline === undefined) return null;
  const items = [
    { label: "Number of records", before: v.records_baseline, after: v.records_current, change: v.records_change_pct },
    { label: "Value per record", before: v.value_per_record_baseline, after: v.value_per_record_current, change: v.value_per_record_change_pct },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="rounded-lg border bg-surface px-4 py-3">
          <p className="text-xs text-muted-foreground">{item.label}</p>
          <div className="mt-1 flex items-baseline justify-between gap-2">
            <span className="tabular text-sm">
              {formatNumber(item.before)} <ArrowRight className="inline size-3 text-muted-foreground" /> {formatNumber(item.after)}
            </span>
            <span className={cn("tabular text-sm font-medium", changeTone(item.change ?? 0))}>{formatPercent(item.change ?? null)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function DriverBars({ driver }: { driver: Driver }) {
  const max = Math.max(...driver.top.map((segment) => Math.abs(segment.contribution)), 1);
  return (
    <ul className="space-y-2.5">
      {driver.top.map((segment) => {
        const width = (Math.abs(segment.contribution) / max) * 50;
        const negative = segment.contribution < 0;
        return (
          <li key={segment.segment} className="grid grid-cols-[minmax(80px,140px)_1fr_auto] items-center gap-3 text-[13px]">
            <span className={cn("truncate", segment.segment === driver.leading_segment && "font-medium")} title={segment.segment}>
              {segment.segment}
            </span>
            <span className="relative h-5 rounded bg-muted/50" aria-hidden>
              <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
              <span
                className={cn("absolute inset-y-1 rounded-sm", negative ? "bg-critical/70" : "bg-success/70")}
                style={negative ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }}
              />
            </span>
            <span className="tabular w-36 text-right text-xs text-muted-foreground">
              <span className={cn("font-medium", changeTone(segment.contribution))}>
                {segment.contribution > 0 ? "+" : ""}
                {formatNumber(segment.contribution)}
              </span>{" "}
              · {segment.change_pct === null ? "new" : formatPercent(segment.change_pct)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Drivers({ report }: { report: InvestigationReport }) {
  const drivers = report.drivers.filter((driver) => driver.top.length > 0);
  if (drivers.length === 0) return null;
  const first = drivers[0] as Driver;
  return (
    <Panel icon={Target} title="Where the change came from">
      <Tabs defaultValue={first.dimension}>
        <TabsList className="scrollbar-thin mb-4 max-w-full overflow-x-auto">
          {drivers.map((driver) => (
            <TabsTrigger key={driver.dimension} value={driver.dimension}>
              {humanize(driver.dimension.split(".").pop() ?? driver.dimension)}
            </TabsTrigger>
          ))}
        </TabsList>
        {drivers.map((driver) => (
          <TabsContent key={driver.dimension} value={driver.dimension} className="space-y-3">
            <p className="text-[13px] text-muted-foreground">
              Biggest mover: <span className="font-medium text-foreground">{driver.leading_segment}</span>
              {typeof driver.top[0]?.share_of_change_pct === "number" && driver.top[0].segment === driver.leading_segment
                ? `, ${Math.round(driver.top[0].share_of_change_pct)}% of the total change`
                : ""}
              .
            </p>
            <DriverBars driver={driver} />
          </TabsContent>
        ))}
      </Tabs>
    </Panel>
  );
}

const LIKELIHOOD: Record<string, "critical" | "warning" | "outline"> = {
  "most likely": "critical",
  likely: "warning",
  possible: "outline",
};

function ReasonList({ reasons }: { reasons: Reason[] }) {
  return (
    <ol className="space-y-3">
      {reasons.map((reason, index) => (
        <li key={`${reason.title}-${index}`} className="flex gap-3">
          <span className="tabular flex size-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium text-muted-foreground">{index + 1}</span>
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[13px] font-medium">{stripEmoji(reason.title)}</p>
              {reason.likelihood && <Badge variant={LIKELIHOOD[reason.likelihood.toLowerCase()] ?? "outline"}>{reason.likelihood}</Badge>}
            </div>
            <p className="text-[13px] leading-relaxed text-muted-foreground">{stripEmoji(reason.detail)}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function Evidence({ report }: { report: InvestigationReport }) {
  const facts = report.facts ?? [];
  const queries = report.queries ?? [];
  if (facts.length === 0 && queries.length === 0) return null;
  return (
    <Panel icon={Microscope} title="Evidence" aside={<span className="text-xs text-muted-foreground">{queries.length} queries ran</span>}>
      {facts.length > 0 && (
        <ul className="space-y-1.5">
          {facts.map((fact, index) => (
            <li key={index} className="flex gap-2 text-[13px] text-muted-foreground">
              <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-primary/70" /> <span>{stripEmoji(fact)}</span>
            </li>
          ))}
        </ul>
      )}
      {queries.length > 0 && (
        <div className={cn("space-y-2", facts.length > 0 && "mt-4 border-t pt-4")}>
          {queries.map((query, index) => (
            <details key={index} className="group rounded-lg border">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-[13px] [&::-webkit-details-marker]:hidden">
                <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" />
                <Code2 className="size-3.5 text-muted-foreground" />
                {query.purpose}
              </summary>
              <div className="border-t p-3">
                <CodeBlock code={query.sql} />
              </div>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

export function InvestigationView({ report, onAsk, busy }: { report: InvestigationReport; onAsk: (question: string) => void; busy?: boolean }) {
  const narrative = report.narrative ?? {};
  const reasons = report.reasons ?? [];
  const ruledOut = report.ruled_out ?? [];
  const actions = narrative.actions ?? [];
  const nextSteps = narrative.next_steps ?? [];
  const hypotheses = narrative.hypotheses ?? [];
  const trace = report.trace;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="primary">
          <Compass /> Investigation
        </Badge>
        <TrustBadge trust={report.trust} />
        <span className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <Database className="size-3.5" /> {report.plan.fact_table}
          </span>
          {trace?.seconds !== undefined && (
            <span className="inline-flex items-center gap-1.5">
              <Clock className="size-3.5" /> {formatSeconds(trace.seconds)}
            </span>
          )}
          {trace?.id && (
            <Link href={`/traces?id=${encodeURIComponent(trace.id)}`} className="inline-flex items-center gap-1.5 hover:text-foreground">
              <Activity className="size-3.5" /> Trace
            </Link>
          )}
        </span>
      </div>

      {(narrative.real_reason || narrative.summary) && (
        <div className="fx-beam fx-glow fx-enter relative rounded-xl border border-primary/25 bg-gradient-to-br from-primary/[0.09] via-card to-card p-5">
          <div className="flex gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/15">
              <Sparkles className="size-4 text-primary" />
            </span>
            <div className="min-w-0 space-y-1.5">
              <p className="text-xs font-medium uppercase tracking-wider text-primary">Real reason</p>
              <p className="text-[15px] font-medium leading-relaxed">{stripEmoji(narrative.real_reason || narrative.summary || "")}</p>
              {narrative.real_reason && narrative.summary && <RichText text={narrative.summary} className="text-sm text-muted-foreground" />}
            </div>
          </div>
        </div>
      )}

      <Headline report={report} />
      <VolumeValue report={report} />
      <Trend report={report} />
      <Drivers report={report} />

      <div className="grid gap-4 lg:grid-cols-2">
        {reasons.length > 0 && (
          <Panel icon={Lightbulb} title="Likely causes" aside={<span className="whitespace-nowrap text-xs text-muted-foreground">Linked, not proven</span>}>
            <ReasonList reasons={reasons} />
          </Panel>
        )}
        {ruledOut.length > 0 && (
          <Panel icon={CircleSlash} title="Ruled out">
            <ul className="space-y-3">
              {ruledOut.map((reason, index) => (
                <li key={index} className="flex gap-3">
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
                  <div className="space-y-0.5">
                    <p className="text-[13px] font-medium">{stripEmoji(reason.title)}</p>
                    <p className="text-[13px] text-muted-foreground">{stripEmoji(reason.detail)}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>

      {(actions.length > 0 || nextSteps.length > 0 || hypotheses.length > 0) && (
        <Panel icon={ListChecks} title="What to do next">
          <div className="space-y-4">
            {actions.length > 0 && (
              <ul className="space-y-2.5">
                {actions.map((action, index) => (
                  <li key={index} className="flex gap-3 text-[13px]">
                    <span className="tabular flex size-5 shrink-0 items-center justify-center rounded bg-primary/15 text-[11px] font-semibold text-primary">{index + 1}</span>
                    <span>
                      <span className="font-medium">{stripEmoji(action.action)}</span>
                      {action.why && <span className="text-muted-foreground"> {stripEmoji(action.why)}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {hypotheses.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">Ideas to check</p>
                <ul className="space-y-1.5">
                  {hypotheses.map((item, index) => (
                    <li key={index} className="text-[13px]">
                      {stripEmoji(item.hypothesis)}
                      {item.evidence && <span className="text-muted-foreground"> ({stripEmoji(item.evidence)})</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          {nextSteps.length > 0 && (
            <div className="mt-4 border-t pt-4">
              <FollowUps items={nextSteps} onAsk={onAsk} disabled={busy} />
            </div>
          )}
        </Panel>
      )}

      <Evidence report={report} />
    </div>
  );
}
