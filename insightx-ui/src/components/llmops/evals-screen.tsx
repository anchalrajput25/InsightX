"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  BadgeCheck,
  CircleSlash,
  FileBarChart2,
  FlaskConical,
  GitCompareArrows,
  ListChecks,
  Loader2,
  Play,
  ShieldCheck,
  Square,
  Target,
  Timer,
  XCircle,
} from "lucide-react";
import {
  axisProps,
  ChartTooltip,
  gridProps,
} from "@/components/charts/chart-kit";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import {
  CardsSkeleton,
  EmptyState,
  ErrorState,
  RowsSkeleton,
} from "@/components/common/states";
import {
  LevelBadge,
  Meter,
  pct,
  ScoreRing,
  secs,
  Segmented,
} from "@/components/llmops/llmops-kit";
import { useConnection } from "@/components/providers/connection-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { errorMessage } from "@/lib/api/client";
import {
  llmopsApi,
  type EvalJob,
  type GateTargets,
  type ReportItem,
} from "@/lib/api/llmops";
import { formatDateTime, formatNumber, humanize, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const GATE_LABELS: Record<string, string> = {
  execution_accuracy: "Execution accuracy",
  answer_faithfulness: "Answer faithfulness",
  safety_pass_rate: "Safety pass rate",
  latency_p95_s: "Latency p95",
};

function gateValue(metric: string, value: number | null): string {
  if (value === null || value === undefined) return "-";
  return metric === "latency_p95_s" ? secs(value) : pct(value, 1);
}

function GateBadge({ passed, known }: { passed: boolean; known: boolean }) {
  if (!known) return <Badge variant="outline">No gate data</Badge>;
  return passed ? (
    <Badge variant="success">
      <BadgeCheck /> Gate passed
    </Badge>
  ) : (
    <Badge variant="critical">
      <XCircle /> Gate failed
    </Badge>
  );
}

function reportLabel(report: ReportItem): string {
  return `${report.golden_set} · ${report.run_at ? formatDateTime(report.run_at) : report.name}${report.scope === "mine" ? " (yours)" : ""}`;
}

// ------------------------------------------------------------------ report
function ReportDetail({ id }: { id: string }) {
  const [onlyFailed, setOnlyFailed] = React.useState(true);
  const report = useQuery({
    queryKey: ["llmops", "report", id],
    queryFn: () => llmopsApi.report(id),
  });
  if (report.isPending) return <CardsSkeleton count={4} />;
  if (report.isError)
    return (
      <ErrorState error={report.error} onRetry={() => void report.refetch()} />
    );
  const r = report.data;
  const s = r.summary ?? {};
  const ci = s.execution_accuracy_ci;
  const byDifficulty = Object.entries(s.by_difficulty ?? {}).map(
    ([name, v]) => ({
      name,
      accuracy: (v.accuracy ?? 0) * 100,
      cases: v.cases ?? 0,
    }),
  );
  const diagnoses = Object.entries(s.diagnoses ?? {}).map(([name, count]) => ({
    name: humanize(name),
    count,
  }));
  const stages = Object.entries(s.stage_mean_s ?? {}).slice(0, 8);
  const maxStage = Math.max(...stages.map(([, v]) => v), 0.001);
  const cases = (r.cases ?? []).filter((c) => !onlyFailed || !c.passed);

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <SpotlightCard className="flex flex-col items-center gap-3 rounded-xl border bg-card p-5 text-center">
          <ScoreRing
            value={s.execution_accuracy ?? null}
            size={140}
            label="accuracy"
          />
          {ci && ci[0] !== null && (
            <p className="text-xs text-muted-foreground">
              95% range {pct(ci[0])} to {pct(ci[1])}
            </p>
          )}
          <GateBadge
            passed={r.gate.passed}
            known={r.gate.checks.some((c) => c.passed !== null)}
          />
          <p className="text-[11px] text-muted-foreground">
            {r.golden_set} on {r.database || "unknown database"}
            <br />
            {r.case_count} cases, {r.repeats} repeat(s), {secs(r.duration_s)}
          </p>
          {r.stopped && (
            <p className="text-[11px] text-warning">
              Stopped early: {r.stopped}
            </p>
          )}
        </SpotlightCard>
        <div className="space-y-4">
          <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="Strict accuracy"
              value={pct(s.strict_accuracy, 1)}
              icon={Target}
            />
            <StatCard
              label="Answer faithfulness"
              value={pct(s.answer_faithfulness, 1)}
              icon={ShieldCheck}
              tone="primary"
            />
            <StatCard
              label="Safety pass rate"
              value={pct(s.safety_pass_rate, 1)}
              icon={CircleSlash}
              tone="success"
            />
            <StatCard
              label="Latency p50 / p95"
              value={secs(s.latency_p50_s)}
              icon={Timer}
              hint={`p95 ${secs(s.latency_p95_s)}`}
            />
          </div>
          <div className="rounded-xl border bg-card p-4">
            <SectionTitle>Quality gate</SectionTitle>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {r.gate.checks.map((check) => (
                <div
                  key={check.metric}
                  className={cn(
                    "rounded-lg border p-3",
                    check.passed === false &&
                      "border-critical/40 bg-critical/[0.05]",
                    check.passed && "border-success/30",
                  )}
                >
                  <p className="text-[11px] text-muted-foreground">
                    {GATE_LABELS[check.metric] ?? check.metric}
                  </p>
                  <p className="tabular text-lg font-semibold">
                    {gateValue(check.metric, check.value)}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    needs{" "}
                    {check.metric === "latency_p95_s" ? "at most" : "at least"}{" "}
                    {gateValue(check.metric, check.target)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border bg-card p-4">
          <SectionTitle>Accuracy by difficulty</SectionTitle>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={byDifficulty}
                margin={{ top: 6, right: 8, bottom: 0, left: 0 }}
              >
                <CartesianGrid {...gridProps} />
                <XAxis {...axisProps} dataKey="name" />
                <YAxis {...axisProps} width={44} domain={[0, 100]} unit="%" />
                <Tooltip
                  content={<ChartTooltip />}
                  cursor={{ fill: "var(--accent)", opacity: 0.4 }}
                />
                <Bar
                  dataKey="accuracy"
                  name="Accuracy %"
                  radius={[4, 4, 0, 0]}
                  maxBarSize={40}
                >
                  {byDifficulty.map((row) => (
                    <Cell
                      key={row.name}
                      fill={
                        row.accuracy >= 85
                          ? "var(--success)"
                          : row.accuracy >= 60
                            ? "var(--warning)"
                            : "var(--critical)"
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <SectionTitle>Why cases failed</SectionTitle>
          {diagnoses.length === 0 ? (
            <p className="py-16 text-center text-xs text-muted-foreground">
              No failures.
            </p>
          ) : (
            <ul className="space-y-2">
              {diagnoses.slice(0, 7).map((d) => (
                <li
                  key={d.name}
                  className="flex items-center justify-between gap-2 text-xs"
                >
                  <span className="truncate">{d.name}</span>
                  <Badge variant="critical">{d.count}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-xl border bg-card p-4">
          <SectionTitle>Mean time per step</SectionTitle>
          <ul className="space-y-2">
            {stages.map(([name, value]) => (
              <li
                key={name}
                className="grid grid-cols-[110px_1fr_48px] items-center gap-2 text-xs"
              >
                <span className="truncate text-muted-foreground">
                  {humanize(name)}
                </span>
                <span className="h-1.5 rounded-full bg-muted">
                  <span
                    className="block h-1.5 rounded-full bg-primary/70"
                    style={{ width: `${(value / maxStage) * 100}%` }}
                  />
                </span>
                <span className="tabular text-right">{secs(value)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <section>
        <SectionTitle
          aside={
            <Segmented
              label="Cases"
              value={onlyFailed ? "failed" : "all"}
              onChange={(v) => setOnlyFailed(v === "failed")}
              options={[
                { value: "failed", label: "Failed" },
                { value: "all", label: "All" },
              ]}
            />
          }
        >
          Cases ({cases.length})
        </SectionTitle>
        {cases.length === 0 ? (
          <EmptyState compact icon={BadgeCheck} title="Every case passed" />
        ) : (
          <ul className="divide-y overflow-hidden rounded-xl border bg-card">
            {cases.slice(0, 200).map((c, index) => (
              <li
                key={`${c.id}-${c.variant}-${c.repeat}-${index}`}
                className="px-4 py-3"
              >
                <details>
                  <summary className="flex cursor-pointer list-none items-center gap-3">
                    <span
                      className={cn(
                        "size-2 shrink-0 rounded-full",
                        c.passed ? "bg-success" : "bg-critical",
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate text-sm">
                      {c.question}
                    </span>
                    <LevelBadge level={c.difficulty} />
                    {c.variant !== "baseline" && (
                      <Badge variant="outline">{c.variant}</Badge>
                    )}
                    <span className="tabular hidden w-14 text-right text-xs text-muted-foreground sm:block">
                      {secs(c.latency_s)}
                    </span>
                  </summary>
                  <div className="mt-3 space-y-2 text-xs">
                    {!c.passed && (
                      <p className="text-critical">
                        {humanize(c.diagnosis || c.reason || c.status)}
                      </p>
                    )}
                    <div className="grid gap-2 md:grid-cols-2">
                      <pre className="overflow-x-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
                        {c.expected_sql || "(no expected SQL)"}
                      </pre>
                      <pre className="overflow-x-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] whitespace-pre-wrap">
                        {c.predicted_sql || "(no SQL produced)"}
                      </pre>
                    </div>
                    <p className="text-muted-foreground">
                      Left: expected. Right: what the agent wrote. Models used:{" "}
                      {Object.keys(c.models ?? {}).join(", ") || "-"}
                    </p>
                  </div>
                </details>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ----------------------------------------------------------------- compare
function CompareView({ reports }: { reports: ReportItem[] }) {
  const [base, setBase] = React.useState(reports[1]?.id ?? "");
  const [other, setOther] = React.useState(reports[0]?.id ?? "");
  const diff = useQuery({
    queryKey: ["llmops", "compare", base, other],
    queryFn: () => llmopsApi.compare(base, other),
    enabled: Boolean(base && other && base !== other),
  });

  if (reports.length < 2)
    return (
      <EmptyState
        icon={GitCompareArrows}
        title="Two reports needed"
        description="Run at least two evaluations to compare them and spot regressions."
      />
    );
  const picker = (
    value: string,
    onChange: (v: string) => void,
    label: string,
  ) => (
    <label className="min-w-0 flex-1 space-y-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {reports.map((r) => (
            <SelectItem key={r.id} value={r.id}>
              {reportLabel(r)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row">
        {picker(base, setBase, "Before")}
        {picker(other, setOther, "After")}
      </div>
      {base === other ? (
        <p className="text-xs text-muted-foreground">
          Pick two different reports.
        </p>
      ) : diff.isPending ? (
        <RowsSkeleton rows={6} />
      ) : diff.isError ? (
        <ErrorState error={diff.error} />
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border bg-card">
            <table className="w-full text-sm">
              <thead className="bg-surface text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">Metric</th>
                  <th className="px-3 py-2 text-right font-medium">Before</th>
                  <th className="px-3 py-2 text-right font-medium">After</th>
                  <th className="px-3 py-2 text-right font-medium">Change</th>
                </tr>
              </thead>
              <tbody>
                {diff.data.metrics.map((m) => {
                  const isTime = m.key.includes("latency");
                  const isCost = m.key.includes("cost");
                  const show = (v: number | null) =>
                    v === null
                      ? "-"
                      : isTime
                        ? secs(v)
                        : isCost
                          ? `$${v.toFixed(4)}`
                          : pct(v, 1);
                  return (
                    <tr key={m.key} className="border-t">
                      <td className="px-4 py-2">{m.label}</td>
                      <td className="tabular px-3 py-2 text-right">
                        {show(m.base)}
                      </td>
                      <td className="tabular px-3 py-2 text-right">
                        {show(m.other)}
                      </td>
                      <td
                        className={cn(
                          "tabular px-3 py-2 text-right",
                          m.improved === true && "text-success",
                          m.improved === false && "text-critical",
                        )}
                      >
                        {m.delta === null
                          ? "-"
                          : `${m.delta > 0 ? "+" : ""}${isTime ? m.delta.toFixed(2) + " s" : isCost ? m.delta.toFixed(4) : (m.delta * 100).toFixed(1) + " pts"}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {[
              {
                title: "Regressions: passed before, fail now",
                rows: diff.data.regressions,
                tone: "critical" as const,
              },
              {
                title: "Fixed: failed before, pass now",
                rows: diff.data.fixes,
                tone: "success" as const,
              },
            ].map((block) => (
              <section key={block.title}>
                <SectionTitle>
                  {block.title} ({block.rows.length})
                </SectionTitle>
                {block.rows.length === 0 ? (
                  <p className="rounded-xl border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
                    None.
                  </p>
                ) : (
                  <ul className="divide-y overflow-hidden rounded-xl border bg-card">
                    {block.rows.map((row) => (
                      <li
                        key={row.id}
                        className="flex items-center gap-3 px-4 py-2.5"
                      >
                        <span
                          className={cn(
                            "size-2 rounded-full",
                            block.tone === "critical"
                              ? "bg-critical"
                              : "bg-success",
                          )}
                        />
                        <span className="min-w-0 flex-1 truncate text-sm">
                          {row.question}
                        </span>
                        <span className="tabular text-xs text-muted-foreground">
                          {pct(row.base)} to {pct(row.other)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- run
function JobCard({ job, onStop }: { job: EvalJob; onStop: () => void }) {
  const { total, done, passed } = job.progress;
  const share = total ? done / total : 0;
  const running = job.state === "running";
  return (
    <SpotlightCard
      className={cn("rounded-xl border bg-card p-4", running && "fx-beam")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {job.golden_id.split(":")[1]?.replace(".yaml", "")}
          </p>
          <p className="text-[11px] text-muted-foreground">
            {job.database} · started {timeAgo(job.started)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant={
              running
                ? "info"
                : job.state === "finished"
                  ? "success"
                  : job.state === "stopped"
                    ? "outline"
                    : "critical"
            }
            className="capitalize"
          >
            {running && <Loader2 className="animate-spin" />}
            {job.state}
          </Badge>
          {running && (
            <Button size="xs" variant="outline" onClick={onStop}>
              <Square /> Stop
            </Button>
          )}
          {job.report_id && (
            <Button size="xs" variant="outline" asChild>
              <Link href={`/evals?report=${encodeURIComponent(job.report_id)}`}>
                Open report
              </Link>
            </Button>
          )}
        </div>
      </div>
      <div className="mt-3 space-y-1">
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>
            {done}
            {total ? ` of ${total}` : ""} questions, {passed} passed
          </span>
          <span className="tabular">{total ? pct(share) : ""}</span>
        </div>
        <Meter value={share} tone="primary" />
      </div>
      {job.progress.recent.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {job.progress.recent.map((item, index) => (
            <span
              key={`${item.id}-${index}`}
              title={item.line}
              className={cn(
                "rounded px-1.5 py-0.5 font-mono text-[10px]",
                item.passed
                  ? "bg-success/12 text-success"
                  : "bg-critical/12 text-critical",
              )}
            >
              {item.id}
            </span>
          ))}
        </div>
      )}
      {!running && job.state === "failed" && job.progress.tail.length > 0 && (
        <pre className="mt-3 max-h-32 overflow-auto rounded-md bg-muted/50 p-2 text-[11px] whitespace-pre-wrap text-critical">
          {job.progress.tail.join("\n")}
        </pre>
      )}
    </SpotlightCard>
  );
}

function RunView() {
  const queryClient = useQueryClient();
  const { connection } = useConnection();
  const golden = useQuery({
    queryKey: ["llmops", "golden"],
    queryFn: () => llmopsApi.golden(),
  });
  const runs = useQuery({
    queryKey: ["llmops", "runs"],
    queryFn: () => llmopsApi.runs(),
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => r.state === "running") ? 2000 : 15_000,
  });
  const [goldenId, setGoldenId] = React.useState("");
  const [repeats, setRepeats] = React.useState(1);
  const [difficulty, setDifficulty] = React.useState("");
  const [ablation, setAblation] = React.useState(false);
  const [openSet, setOpenSet] = React.useState<string | null>(null);
  const cases = useQuery({
    queryKey: ["llmops", "golden", openSet],
    queryFn: () => llmopsApi.goldenCases(openSet!),
    enabled: Boolean(openSet),
  });

  React.useEffect(() => {
    if (!goldenId && golden.data?.golden.length)
      setGoldenId(golden.data.golden.find((g) => !g.error)?.id ?? "");
  }, [golden.data, goldenId]);

  const start = useMutation({
    mutationFn: () =>
      llmopsApi.startRun({
        connection_id: connection!.id,
        golden_id: goldenId,
        repeats,
        workers: 1,
        difficulty,
        ablation,
      }),
    onSuccess: () => {
      toast.success("Evaluation started");
      void queryClient.invalidateQueries({ queryKey: ["llmops", "runs"] });
    },
    onError: (error) =>
      toast.error("Could not start", { description: errorMessage(error) }),
  });
  const stop = useMutation({
    mutationFn: (id: string) => llmopsApi.stopRun(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["llmops", "runs"] });
      void queryClient.invalidateQueries({ queryKey: ["llmops", "reports"] });
    },
  });

  const lastFinished = React.useRef<string>("");
  React.useEffect(() => {
    const done = runs.data?.runs.find((r) => r.state !== "running");
    if (done && done.id !== lastFinished.current) {
      lastFinished.current = done.id;
      void queryClient.invalidateQueries({ queryKey: ["llmops", "reports"] });
    }
  }, [runs.data, queryClient]);

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
        <div className="space-y-4 rounded-xl border bg-card p-4">
          <SectionTitle>Start an evaluation</SectionTitle>
          {!connection ? (
            <p className="text-xs text-warning">
              Connect a PostgreSQL or MySQL database first on the{" "}
              <Link href="/connect" className="underline">
                Database
              </Link>{" "}
              page.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Runs against{" "}
              <span className="text-foreground">{connection.label}</span>. Pick
              the golden set that matches this database.
            </p>
          )}
          <label className="block space-y-1 text-xs text-muted-foreground">
            <span>Golden set</span>
            <Select value={goldenId} onValueChange={setGoldenId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Choose a golden set" />
              </SelectTrigger>
              <SelectContent>
                {(golden.data?.golden ?? [])
                  .filter((g) => !g.error)
                  .map((g) => (
                    <SelectItem key={g.id} value={g.id}>
                      {g.name} ({g.cases} cases)
                      {g.scope === "mine" ? ", yours" : ""}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-xs text-muted-foreground">
              <span>Repeats</span>
              <Segmented
                label="Repeats"
                value={repeats}
                onChange={setRepeats}
                options={[1, 2, 3].map((n) => ({ value: n, label: String(n) }))}
              />
            </label>
            <label className="space-y-1 text-xs text-muted-foreground">
              <span>Difficulty</span>
              <Select
                value={difficulty || "all"}
                onValueChange={(v) => setDifficulty(v === "all" ? "" : v)}
              >
                <SelectTrigger className="h-8 w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["all", "easy", "medium", "hard", "extra"].map((d) => (
                    <SelectItem key={d} value={d} className="capitalize">
                      {d}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          </div>
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={ablation}
              onChange={(event) => setAblation(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              Ablation study
              <span className="block text-muted-foreground">
                Also runs the pipeline with parts switched off (one SQL
                candidate, no correction) to show what each part is worth. Takes
                about five times longer.
              </span>
            </span>
          </label>
          <Button
            className="w-full"
            onClick={() => start.mutate()}
            disabled={
              !connection ||
              !goldenId ||
              start.isPending ||
              runs.data?.runs.some((r) => r.state === "running")
            }
          >
            {start.isPending ? <Loader2 className="animate-spin" /> : <Play />}{" "}
            Run evaluation
          </Button>
          <p className="text-[11px] text-muted-foreground">
            Each question uses your free AI models. Saved answers are not
            reused, so the run measures the models as they are now.
          </p>
        </div>
        <div className="space-y-3">
          <SectionTitle>Runs</SectionTitle>
          {runs.isPending ? (
            <RowsSkeleton rows={3} />
          ) : (runs.data?.runs ?? []).length === 0 ? (
            <EmptyState
              compact
              icon={FlaskConical}
              title="No runs yet"
              description="Started runs show live progress here, case by case."
            />
          ) : (
            runs.data!.runs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                onStop={() => stop.mutate(job.id)}
              />
            ))
          )}
        </div>
      </div>

      <section>
        <SectionTitle>Golden sets</SectionTitle>
        {golden.isPending ? (
          <RowsSkeleton rows={3} />
        ) : (
          <div className="fx-stagger grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(golden.data?.golden ?? []).map((g) => (
              <button
                key={g.id}
                type="button"
                onClick={() => setOpenSet(openSet === g.id ? null : g.id)}
                className="text-left"
              >
                <SpotlightCard
                  className={cn(
                    "h-full rounded-xl border bg-card p-4 transition-colors hover:border-foreground/15",
                    openSet === g.id && "border-primary/50",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-medium">{g.name}</p>
                    <Badge variant={g.scope === "mine" ? "primary" : "outline"}>
                      {g.scope === "mine" ? "From feedback" : "Built in"}
                    </Badge>
                  </div>
                  {g.error ? (
                    <p className="mt-1 text-xs text-critical">{g.error}</p>
                  ) : (
                    <>
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                        {g.description || "No description."}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1">
                        <Badge variant="default">{g.cases} cases</Badge>
                        {Object.entries(g.by_difficulty ?? {}).map(([d, n]) => (
                          <Badge
                            key={d}
                            variant="outline"
                            className="capitalize"
                          >
                            {d} {n}
                          </Badge>
                        ))}
                      </div>
                    </>
                  )}
                </SpotlightCard>
              </button>
            ))}
          </div>
        )}
        {openSet && (
          <div className="mt-3 rounded-xl border bg-card">
            {cases.isPending ? (
              <div className="p-4">
                <RowsSkeleton rows={4} />
              </div>
            ) : cases.isError ? (
              <ErrorState error={cases.error} />
            ) : (
              <ul className="max-h-96 divide-y overflow-y-auto">
                {cases.data.cases.map((c) => (
                  <li
                    key={c.id}
                    className="flex items-center gap-3 px-4 py-2 text-sm"
                  >
                    <span className="w-24 shrink-0 truncate font-mono text-[11px] text-muted-foreground">
                      {c.id}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      {c.question}
                    </span>
                    <LevelBadge level={c.difficulty} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

// -------------------------------------------------------------------- gate
function GateEditor({
  gate,
  onSaved,
}: {
  gate: GateTargets;
  onSaved: () => void;
}) {
  const [draft, setDraft] = React.useState({
    execution_accuracy: String(gate.execution_accuracy * 100),
    answer_faithfulness: String(gate.answer_faithfulness * 100),
    safety_pass_rate: String(gate.safety_pass_rate * 100),
    latency_p95_s: String(gate.latency_p95_s),
  });
  const save = useMutation({
    mutationFn: () =>
      llmopsApi.saveGate({
        execution_accuracy: Number(draft.execution_accuracy) / 100,
        answer_faithfulness: Number(draft.answer_faithfulness) / 100,
        safety_pass_rate: Number(draft.safety_pass_rate) / 100,
        latency_p95_s: Number(draft.latency_p95_s),
      }),
    onSuccess: () => {
      toast.success("Quality gate saved");
      onSaved();
    },
    onError: (error) =>
      toast.error("Could not save", { description: errorMessage(error) }),
  });
  return (
    <div className="space-y-4 rounded-xl border bg-card p-4">
      <p className="text-sm text-muted-foreground">
        A report passes the gate when every number meets its target. Use it
        before you change prompts or models: run an evaluation, and only keep
        the change if the gate still passes.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(Object.keys(draft) as (keyof typeof draft)[]).map((key) => (
          <label key={key} className="space-y-1 text-xs text-muted-foreground">
            <span>
              {GATE_LABELS[key]}{" "}
              {key === "latency_p95_s" ? "at most (s)" : "at least (%)"}
            </span>
            <input
              type="number"
              step="any"
              value={draft[key]}
              onChange={(event) =>
                setDraft((d) => ({ ...d, [key]: event.target.value }))
              }
              className="tabular h-9 w-full rounded-md border bg-background px-3 text-sm text-foreground"
            />
          </label>
        ))}
      </div>
      <Button onClick={() => save.mutate()} disabled={save.isPending}>
        {save.isPending && <Loader2 className="animate-spin" />} Save gate
      </Button>
    </div>
  );
}

// ------------------------------------------------------------------ screen
export function EvalsScreen() {
  const queryClient = useQueryClient();
  const reports = useQuery({
    queryKey: ["llmops", "reports"],
    queryFn: () => llmopsApi.reports(),
  });
  const [selected, setSelected] = React.useState<string>("");
  const [tab, setTab] = React.useState("reports");
  const list = React.useMemo(() => reports.data?.reports ?? [], [reports.data]);

  React.useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("report");
    if (wanted) {
      setSelected(wanted);
      setTab("reports");
    }
  }, []);
  React.useEffect(() => {
    if (!selected && list.length) setSelected(list[0]!.id);
  }, [list, selected]);

  const latest = list[0];
  const passing = list.filter((r) => r.gate.passed).length;
  const trend = [...list]
    .reverse()
    .filter((r) => r.execution_accuracy !== null)
    .map((r) => ({
      name: r.golden_set,
      at: r.run_at,
      accuracy: (r.execution_accuracy ?? 0) * 100,
    }));

  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="Evals"
        description="Measure the agent on golden questions with known correct SQL: accuracy, faithfulness, safety and speed, with a quality gate and regression checks between runs."
      />

      {reports.isPending ? (
        <CardsSkeleton count={4} />
      ) : reports.isError ? (
        <ErrorState
          error={reports.error}
          onRetry={() => void reports.refetch()}
        />
      ) : (
        <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            label="Latest accuracy"
            value={pct(latest?.execution_accuracy, 1)}
            icon={Target}
            tone="primary"
            hint={
              latest
                ? `${latest.golden_set}, ${timeAgo(latest.modified)}`
                : "No runs yet"
            }
          />
          <StatCard
            label="Latest faithfulness"
            value={pct(latest?.answer_faithfulness, 1)}
            icon={ShieldCheck}
          />
          <StatCard
            label="Reports"
            value={formatNumber(list.length)}
            icon={FileBarChart2}
            hint={`${passing} passed the gate`}
          />
          <StatCard
            label="Gate target"
            value={pct(reports.data?.gate.execution_accuracy)}
            icon={ListChecks}
            hint="minimum accuracy"
          />
        </div>
      )}

      {trend.length > 1 && (
        <div className="rounded-xl border bg-card p-4">
          <SectionTitle>Accuracy across runs</SectionTitle>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={trend}
                margin={{ top: 6, right: 8, bottom: 0, left: 0 }}
              >
                <CartesianGrid {...gridProps} />
                <XAxis
                  {...axisProps}
                  dataKey="at"
                  tickFormatter={(v) => formatDateTime(String(v))}
                  minTickGap={24}
                />
                <YAxis {...axisProps} width={44} domain={[0, 100]} unit="%" />
                <Tooltip
                  content={
                    <ChartTooltip
                      labelFormatter={(v) => formatDateTime(String(v))}
                    />
                  }
                  cursor={{ fill: "var(--accent)", opacity: 0.4 }}
                />
                <Bar
                  dataKey="accuracy"
                  name="Accuracy %"
                  maxBarSize={28}
                  radius={[3, 3, 0, 0]}
                >
                  {trend.map((row, index) => (
                    <Cell
                      key={index}
                      fill={
                        row.accuracy >=
                        (reports.data?.gate.execution_accuracy ?? 0.7) * 100
                          ? "var(--chart-2)"
                          : "var(--critical)"
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <div className="overflow-x-auto">
          <TabsList>
            <TabsTrigger value="reports">Reports</TabsTrigger>
            <TabsTrigger value="compare">Compare</TabsTrigger>
            <TabsTrigger value="run">Run and golden sets</TabsTrigger>
            <TabsTrigger value="gate">Quality gate</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="reports" className="mt-5 space-y-5">
          {list.length === 0 ? (
            <EmptyState
              icon={FlaskConical}
              title="No evaluation reports yet"
              description="Start one from the Run tab, or run python -m backend.evaluation on your PC. Reports appear here."
              action={
                <Button onClick={() => setTab("run")}>Run an evaluation</Button>
              }
            />
          ) : (
            <>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <span className="text-xs text-muted-foreground">Report</span>
                <Select value={selected} onValueChange={setSelected}>
                  <SelectTrigger className="w-full sm:w-[420px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {list.map((r) => (
                      <SelectItem key={r.id} value={r.id}>
                        {reportLabel(r)} · {pct(r.execution_accuracy)}{" "}
                        {r.gate.passed ? "" : "(gate failed)"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {selected && <ReportDetail id={selected} />}
            </>
          )}
        </TabsContent>
        <TabsContent value="compare" className="mt-5">
          <CompareView reports={list} />
        </TabsContent>
        <TabsContent value="run" className="mt-5">
          <RunView />
        </TabsContent>
        <TabsContent value="gate" className="mt-5">
          {reports.data && (
            <GateEditor
              gate={reports.data.gate}
              onSaved={() =>
                void queryClient.invalidateQueries({ queryKey: ["llmops"] })
              }
            />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
