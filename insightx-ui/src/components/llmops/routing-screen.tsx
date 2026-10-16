"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer, Tooltip } from "recharts";
import { ArrowRight, Brain, Eye, Gauge, Loader2, Power, Route as RouteIcon, Shuffle, Sparkles, Wand2 } from "lucide-react";
import { ChartTooltip, Legend, SERIES } from "@/components/charts/chart-kit";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { LevelBadge, pct, Segmented } from "@/components/llmops/llmops-kit";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/client";
import { llmopsApi, type Level, type RoutingMode, type ScoredRoute } from "@/lib/api/llmops";
import { formatDateTime, formatNumber, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

const MODES: { value: RoutingMode; title: string; text: string; icon: typeof Power }[] = [
  { value: "off", title: "Off", text: "The fixed model order from your settings. Nothing changes.", icon: Power },
  { value: "shadow", title: "Shadow", text: "Scores every call and logs the order it would pick, but still uses the fixed order. Safe to try.", icon: Eye },
  { value: "on", title: "On", text: "Tries models in the scored order. Failover, free limits and health checks still apply.", icon: Sparkles },
];

const PART_LABELS: Record<string, string> = { accuracy: "Accuracy", success: "Success", speed: "Speed", headroom: "Free quota left" };

function shortRoute(route: string): string {
  const [provider, ...rest] = route.split("/");
  const model = rest.join("/");
  return `${model.split("/").pop()} (${provider})`;
}

function RankedList({ ranked, defaults }: { ranked: ScoredRoute[]; defaults: string[] }) {
  const max = Math.max(...ranked.map((r) => r.score), 0.001);
  const sorted = [...ranked].sort((a, b) => Number(a.blocked) - Number(b.blocked) || b.score - a.score);
  return (
    <ol className="space-y-2">
      {sorted.map((item, index) => {
        const moved = defaults.indexOf(item.route) - index;
        return (
          <li key={item.route} className={cn("rounded-lg border p-2.5", index === 0 && !item.blocked && "border-primary/40 bg-primary/[0.05]", item.blocked && "opacity-60")}>
            <div className="flex items-center gap-2">
              <span className="tabular flex size-5 shrink-0 items-center justify-center rounded bg-muted text-[11px] font-medium">{index + 1}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-[12px]" title={item.route}>
                {shortRoute(item.route)}
              </span>
              {item.blocked ? (
                <Badge variant="critical">Unavailable</Badge>
              ) : moved > 0 ? (
                <Badge variant="success">up {moved}</Badge>
              ) : moved < 0 ? (
                <Badge variant="outline">down {-moved}</Badge>
              ) : null}
              <span className="tabular w-10 text-right text-xs font-medium">{item.score.toFixed(2)}</span>
            </div>
            <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-muted" style={{ width: `${(item.score / max) * 100}%` }}>
              {(Object.keys(PART_LABELS) as (keyof ScoredRoute["parts"])[]).map((part, i) => (
                <span key={part} style={{ flex: item.parts[part], background: SERIES[i] }} title={`${PART_LABELS[part]} ${pct(item.parts[part])}`} />
              ))}
            </div>
            <p className="mt-1 text-[10px] text-muted-foreground">
              {(Object.keys(PART_LABELS) as (keyof ScoredRoute["parts"])[]).map((part) => `${PART_LABELS[part]} ${pct(item.parts[part])}`).join(" · ")}
              {` · ${item.calls_7d} calls, ${item.eval_cases} eval cases`}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

export function RoutingScreen() {
  const queryClient = useQueryClient();
  const [level, setLevel] = React.useState<Level>("medium");
  const [probe, setProbe] = React.useState("");
  const overview = useQuery({ queryKey: ["llmops", "routing"], queryFn: () => llmopsApi.routing(), refetchInterval: 15_000 });
  const decisions = useQuery({ queryKey: ["llmops", "routing", "decisions"], queryFn: () => llmopsApi.decisions(), refetchInterval: 10_000 });
  const classify = useQuery({
    queryKey: ["llmops", "classify", probe],
    queryFn: () => llmopsApi.classify(probe),
    enabled: probe.trim().length > 3,
  });
  const setMode = useMutation({
    mutationFn: (mode: RoutingMode) => llmopsApi.setRouting(mode),
    onSuccess: (data) => {
      toast.success(data.mode === "on" ? "Smart routing is on" : data.mode === "shadow" ? "Smart routing is in shadow mode" : "Smart routing is off");
      void queryClient.invalidateQueries({ queryKey: ["llmops", "routing"] });
    },
    onError: (error) => toast.error("Could not change routing", { description: errorMessage(error) }),
  });

  React.useEffect(() => {
    if (classify.data) setLevel(classify.data.level);
  }, [classify.data]);

  const data = overview.data;
  const weights = data?.weights;
  const radar = weights
    ? Object.keys(PART_LABELS).map((part) => ({
        part: PART_LABELS[part],
        easy: (weights.easy[part] ?? 0) * 100,
        medium: (weights.medium[part] ?? 0) * 100,
        hard: (weights.hard[part] ?? 0) * 100,
      }))
    : [];

  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="Smart routing"
        description="Picks which free model to try first for each AI call, from measured accuracy, success rate, speed and free quota left, weighted by how hard the question is."
      />

      {overview.isPending ? (
        <CardsSkeleton count={3} />
      ) : overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : (
        <>
          <div className="fx-stagger grid gap-3 md:grid-cols-3">
            {MODES.map((mode) => {
              const active = data?.mode === mode.value;
              return (
                <button key={mode.value} type="button" onClick={() => setMode.mutate(mode.value)} disabled={setMode.isPending || active} className="text-left disabled:cursor-default">
                  <SpotlightCard className={cn("h-full rounded-xl border bg-card p-4 transition-colors", active ? "fx-beam border-primary/50" : "hover:border-foreground/15")}>
                    <div className="flex items-center justify-between">
                      <span className={cn("flex size-8 items-center justify-center rounded-md", active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
                        {setMode.isPending && setMode.variables === mode.value ? <Loader2 className="size-4 animate-spin" /> : <mode.icon className="size-4" />}
                      </span>
                      {active && <Badge variant="primary">Active</Badge>}
                    </div>
                    <p className="mt-3 text-sm font-medium">{mode.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{mode.text}</p>
                  </SpotlightCard>
                </button>
              );
            })}
          </div>
          <p className="-mt-4 text-[11px] text-muted-foreground">This setting is shared by everyone using this InsightX server.</p>

          <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Routing decisions" value={formatNumber(data?.summary.decisions)} icon={RouteIcon} hint="Since the server started" />
            <StatCard label="Order changed" value={data?.summary.decisions ? pct((data.summary.changed ?? 0) / data.summary.decisions) : "-"} icon={Shuffle} tone="primary" hint={`${data?.summary.changed ?? 0} calls`} />
            <StatCard label="Models with eval data" value={formatNumber(Object.keys(data?.accuracy ?? {}).length)} icon={Brain} hint="From evaluation reports" />
            <StatCard label="Models with live data" value={formatNumber(Object.keys(data?.stats ?? {}).length)} icon={Gauge} hint="Calls in the last 7 days" />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <section className="min-w-0 space-y-3">
              <SectionTitle aside={<Segmented label="Difficulty" value={level} onChange={setLevel} options={(["easy", "medium", "hard"] as Level[]).map((l) => ({ value: l, label: humanize(l) }))} />}>
                Order it would pick now
              </SectionTitle>
              <div className="flex flex-col gap-2 rounded-xl border bg-card p-3 sm:flex-row sm:items-center">
                <Wand2 className="hidden size-4 shrink-0 text-primary sm:block" />
                <Input value={probe} onChange={(event) => setProbe(event.target.value)} placeholder="Type a question to see how hard it looks" className="h-8 flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0" />
                {classify.data && (
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    <LevelBadge level={classify.data.level} />
                    {classify.data.signals.length ? classify.data.signals.map(humanize).join(", ") : "simple"}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {(data?.preview[level] ?? []).map((tier) => (
                  <div key={tier.tier} className="rounded-xl border bg-card p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <p className="text-sm font-medium capitalize">{tier.tier} tier</p>
                      {tier.fallback_tiers.length > 0 && <span className="text-[10px] text-muted-foreground">then {tier.fallback_tiers.join(", ")}</span>}
                    </div>
                    {tier.ranked.length === 0 ? <p className="text-xs text-muted-foreground">No models.</p> : <RankedList ranked={tier.ranked} defaults={tier.default} />}
                  </div>
                ))}
              </div>
              <Legend items={Object.values(PART_LABELS).map((label, i) => ({ label, color: SERIES[i] as string }))} />
            </section>
            <section className="min-w-0 space-y-3">
              <SectionTitle>Weights by difficulty</SectionTitle>
              <div className="rounded-xl border bg-card p-3">
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <RadarChart data={radar} outerRadius="62%">
                      <PolarGrid stroke="var(--chart-grid)" />
                      <PolarAngleAxis dataKey="part" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <Radar dataKey="easy" name="Easy" stroke="var(--success)" fill="var(--success)" fillOpacity={0.12} />
                      <Radar dataKey="medium" name="Medium" stroke="var(--warning)" fill="var(--warning)" fillOpacity={0.12} />
                      <Radar dataKey="hard" name="Hard" stroke="var(--critical)" fill="var(--critical)" fillOpacity={0.12} />
                      <Tooltip content={<ChartTooltip />} />
                    </RadarChart>
                  </ResponsiveContainer>
                </div>
                <Legend items={[{ label: "Easy", color: "var(--success)" }, { label: "Medium", color: "var(--warning)" }, { label: "Hard", color: "var(--critical)" }]} />
                <p className="mt-2 text-[11px] text-muted-foreground">Easy questions favour fast models with free quota left, saving strong models for hard questions, where accuracy counts most.</p>
              </div>
            </section>
          </div>
        </>
      )}

      <section className="space-y-3">
        <SectionTitle>Recent decisions (your questions)</SectionTitle>
        {(decisions.data?.decisions ?? []).length === 0 ? (
          <EmptyState compact icon={RouteIcon} title="No decisions yet" description="Switch to Shadow or On, then ask a question. Each AI call it scores appears here." />
        ) : (
          <ul className="divide-y overflow-hidden rounded-xl border bg-card">
            {decisions.data!.decisions.slice(0, 40).map((d, index) => (
              <li key={index} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-xs">
                <span className="w-28 shrink-0 text-muted-foreground">{formatDateTime(d.ts)}</span>
                <Badge variant="outline" className="capitalize">
                  {d.tier}
                </Badge>
                <LevelBadge level={d.difficulty} />
                <span className="truncate font-mono text-[11px] text-muted-foreground">{shortRoute(d.default[0] ?? "")}</span>
                <ArrowRight className="size-3 text-muted-foreground" />
                <span className={cn("truncate font-mono text-[11px]", d.changed ? "text-primary" : "text-muted-foreground")}>{shortRoute(d.chosen[0] ?? "")}</span>
                <span className="ml-auto">{d.changed ? <Badge variant="primary">{d.mode === "on" ? "Reordered" : "Would reorder"}</Badge> : <Badge variant="outline">Same order</Badge>}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
