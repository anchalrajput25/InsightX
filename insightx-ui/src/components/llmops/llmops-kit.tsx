"use client";

// Small pieces shared by the LLMOps pages (Monitor, Evals, Hallucination,
// Quality, Routing): segmented switches, badges, a score ring, and the
// answer detail sheet with feedback, AI judge, SQL fix and promote.

import * as React from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDownRight, ArrowUpRight, CheckCircle2, FlaskConical, Gavel, Loader2, Play, ShieldAlert, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { CodeBlock } from "@/components/common/code-block";
import { useConnection } from "@/components/providers/connection-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/client";
import { llmopsApi, type AnswerCheck, type Risk, type SqlTest } from "@/lib/api/llmops";
import { formatDateTime, formatNumber, formatSeconds, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

export function secs(value: number | null | undefined): string {
  return formatSeconds(value ?? null);
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex gap-0.5 rounded-lg bg-muted/60 p-0.5">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "h-7 cursor-pointer rounded-md px-3 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
            value === option.value && "bg-card text-foreground shadow-sm",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

const RISK_VARIANT: Record<Risk, "outline" | "success" | "warning" | "critical" | "info"> = {
  none: "success",
  low: "info",
  medium: "warning",
  high: "critical",
};

export function RiskBadge({ risk }: { risk: Risk | string }) {
  const key = (risk in RISK_VARIANT ? risk : "none") as Risk;
  return <Badge variant={RISK_VARIANT[key]}>{key === "none" ? "Grounded" : `${humanize(key)} risk`}</Badge>;
}

export function LevelBadge({ level }: { level: string }) {
  if (!level) return null;
  const variant = level === "hard" || level === "extra" ? "critical" : level === "medium" ? "warning" : "success";
  return (
    <Badge variant={variant} className="capitalize">
      {level}
    </Badge>
  );
}

export function TrustBadge({ level }: { level: string }) {
  if (!level) return <span className="text-xs text-muted-foreground">-</span>;
  const variant = level === "high" ? "success" : level === "medium" ? "warning" : "critical";
  return (
    <Badge variant={variant} className="capitalize">
      {level} trust
    </Badge>
  );
}

/** A circular score from 0 to 1 with a label in the middle. */
export function ScoreRing({ value, size = 96, label, tone }: { value: number | null | undefined; size?: number; label?: string; tone?: "auto" | "primary" }) {
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circle = 2 * Math.PI * radius;
  const share = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  const color =
    tone === "primary" ? "var(--primary)" : share >= 0.85 ? "var(--success)" : share >= 0.6 ? "var(--warning)" : "var(--critical)";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--muted)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circle}
          strokeDashoffset={circle * (1 - share)}
          style={{ transition: "stroke-dashoffset 900ms cubic-bezier(.2,.8,.2,1)", filter: `drop-shadow(0 0 6px ${color})` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tabular text-lg font-semibold">{value === null || value === undefined ? "-" : pct(value)}</span>
        {label && <span className="text-[10px] text-muted-foreground uppercase">{label}</span>}
      </div>
    </div>
  );
}

/** Change from the previous period, coloured by whether it is good news. */
export function Delta({ now, before, higherIsBetter = true, asPercentPoints = true }: { now: number | null | undefined; before: number | null | undefined; higherIsBetter?: boolean; asPercentPoints?: boolean }) {
  if (now === null || now === undefined || before === null || before === undefined) return null;
  const diff = now - before;
  if (Math.abs(diff) < 1e-9) return <span className="text-muted-foreground">same as before</span>;
  const good = diff > 0 === higherIsBetter;
  const Icon = diff > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cn("inline-flex items-center gap-0.5", good ? "text-success" : "text-critical")}>
      <Icon className="size-3" />
      {asPercentPoints ? `${Math.abs(diff * 100).toFixed(1)} pts` : formatNumber(Math.abs(diff))}
      <span className="text-muted-foreground">&nbsp;vs before</span>
    </span>
  );
}

/** A horizontal bar used in compact tables. */
export function Meter({ value, className, tone }: { value: number | null | undefined; className?: string; tone?: "auto" | "primary" }) {
  const share = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  const color = tone === "primary" ? "bg-primary/80" : share >= 0.85 ? "bg-success" : share >= 0.6 ? "bg-warning" : "bg-critical";
  return (
    <span className={cn("block h-1.5 w-full rounded-full bg-muted", className)}>
      <span className={cn("block h-1.5 rounded-full transition-[width] duration-700", color)} style={{ width: `${share * 100}%` }} />
    </span>
  );
}

const FINDING_LABELS: Record<string, string> = {
  unverified_number: "Number not in result",
  unverified_value: "Value not in result",
  empty_result_claim: "Claims on empty result",
  top_n_mismatch: "Top N larger than result",
  superlative_mismatch: "Wrong highest or lowest",
  row_count_mismatch: "Wrong count",
  fanout: "Join may double count",
  low_trust: "Low trust",
  unknown_name: "Unknown name",
  judge_unsupported: "AI judge: unsupported",
};

export function findingLabel(kind: string): string {
  return FINDING_LABELS[kind] ?? humanize(kind);
}

function PreviewTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (!columns.length) return <p className="text-xs text-muted-foreground">No rows were saved for this answer.</p>;
  return (
    <div className="max-h-56 overflow-auto rounded-lg border">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-surface">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-2 py-1.5 text-left font-medium whitespace-nowrap text-muted-foreground">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t">
              {columns.map((column) => (
                <td key={column} className="tabular px-2 py-1 whitespace-nowrap">
                  {formatNumber(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Highlights the parts of the answer the checks flagged. */
function MarkedAnswer({ answer, findings }: { answer: string; findings: AnswerCheck["checks"]["findings"] }) {
  const marks = findings.map((item) => item.text).filter((text) => text && text.length >= 2 && answer.includes(text));
  if (!marks.length) return <p className="text-sm whitespace-pre-wrap">{answer}</p>;
  const pattern = new RegExp(`(${marks.map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return (
    <p className="text-sm whitespace-pre-wrap">
      {answer.split(pattern).map((part, index) =>
        marks.includes(part) ? (
          <mark key={index} className="rounded bg-critical/20 px-0.5 text-foreground underline decoration-critical decoration-wavy underline-offset-2">
            {part}
          </mark>
        ) : (
          <React.Fragment key={index}>{part}</React.Fragment>
        ),
      )}
    </p>
  );
}

/** Detail of one answer: checks, AI judge, feedback, SQL fix and promote to a golden test. */
export function AnswerSheet({ answerId, onClose }: { answerId: string | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { connection } = useConnection();
  const [answer, setAnswer] = React.useState<AnswerCheck | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [note, setNote] = React.useState("");
  const [sql, setSql] = React.useState("");
  const [test, setTest] = React.useState<SqlTest | null>(null);

  React.useEffect(() => {
    if (!answerId) return;
    let alive = true;
    setLoading(true);
    setTest(null);
    llmopsApi
      .answer(answerId)
      .then((row) => {
        if (!alive) return;
        setAnswer(row);
        setNote(row.note || "");
        setSql(row.fixed_sql || row.sql || "");
      })
      .catch((error) => toast.error("Could not open the answer", { description: errorMessage(error) }))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [answerId]);

  const refresh = (row: AnswerCheck) => {
    setAnswer(row);
    void queryClient.invalidateQueries({ queryKey: ["llmops"] });
  };

  const feedback = useMutation({
    mutationFn: (rating: number) => llmopsApi.feedback(answerId!, rating, note),
    onSuccess: (row) => {
      refresh(row);
      toast.success(row.rating > 0 ? "Marked as a good answer" : row.rating < 0 ? "Marked as a bad answer" : "Feedback saved");
    },
    onError: (error) => toast.error("Could not save feedback", { description: errorMessage(error) }),
  });
  const judge = useMutation({
    mutationFn: () => llmopsApi.judge(answerId!),
    onSuccess: (row) => {
      refresh(row);
      toast.success(row.judge.faithful ? "The AI judge found the answer supported" : "The AI judge found unsupported claims");
    },
    onError: (error) => toast.error("The AI judge could not run", { description: errorMessage(error) }),
  });
  const review = useMutation({
    mutationFn: (state: "open" | "fixed" | "dismissed") => llmopsApi.review(answerId!, state),
    onSuccess: refresh,
    onError: (error) => toast.error("Could not update", { description: errorMessage(error) }),
  });
  const testSql = useMutation({
    mutationFn: () => llmopsApi.testSql(answerId!, connection!.id, sql),
    onSuccess: setTest,
    onError: (error) => toast.error("The SQL did not run", { description: errorMessage(error) }),
  });
  const promote = useMutation({
    mutationFn: () => llmopsApi.promote(answerId!, connection!.id, sql, note),
    onSuccess: (saved) => {
      refresh(saved.answer);
      toast.success(saved.updated ? "Golden test updated" : "Added as a golden test", { description: "Evaluation runs will now check this question." });
    },
    onError: (error) => toast.error("Could not promote", { description: errorMessage(error) }),
  });

  const checks = answer?.checks;
  const findings = checks?.findings ?? [];

  return (
    <Sheet open={Boolean(answerId)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-[640px] max-w-[96vw] overflow-y-auto bg-background" aria-describedby={undefined}>
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b bg-background/90 px-5 py-3 backdrop-blur">
          <SheetTitle className="text-sm font-semibold">Answer check</SheetTitle>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
            <X />
          </Button>
        </div>
        {loading || !answer ? (
          <div className="flex flex-1 items-center justify-center py-24 text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" /> Loading
          </div>
        ) : (
          <div className="space-y-6 px-5 py-5">
            <div className="flex items-start gap-4">
              <ScoreRing value={answer.faithfulness} label="grounded" />
              <div className="min-w-0 space-y-2">
                <p className="text-[15px] leading-snug font-medium">{answer.question}</p>
                <div className="flex flex-wrap gap-1.5">
                  <RiskBadge risk={answer.risk} />
                  <TrustBadge level={answer.trust_level} />
                  <LevelBadge level={answer.difficulty} />
                  <Badge variant="outline">{formatDateTime(answer.ts)}</Badge>
                  {answer.seconds !== null && <Badge variant="outline">{secs(answer.seconds)}</Badge>}
                  {answer.review !== "open" && <Badge variant="primary" className="capitalize">{answer.review}</Badge>}
                </div>
                {answer.trace_id && (
                  <Link href={`/traces?id=${answer.trace_id}`} className="text-xs text-primary hover:underline">
                    Open the trace
                  </Link>
                )}
              </div>
            </div>

            <section className="space-y-2">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Answer</h3>
              <div className="rounded-lg border bg-card p-3">
                <MarkedAnswer answer={answer.answer} findings={findings} />
              </div>
            </section>

            <section className="space-y-2">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Grounding checks</h3>
              {!checks?.applicable ? (
                <p className="text-xs text-muted-foreground">This answer had no result to check against.</p>
              ) : findings.length === 0 ? (
                <p className="flex items-center gap-2 text-sm text-success">
                  <CheckCircle2 className="size-4" /> Every number and value in the answer was found in the result.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {findings.map((item, index) => (
                    <li key={index} className="flex items-start gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
                      <ShieldAlert className={cn("mt-0.5 size-4 shrink-0", item.weight >= 0.25 ? "text-critical" : "text-warning")} />
                      <div className="min-w-0">
                        <p className="text-xs font-medium">{findingLabel(item.kind)}</p>
                        <p className="text-[13px] text-muted-foreground">{item.detail}</p>
                      </div>
                      <span className="tabular ml-auto text-[11px] text-muted-foreground">-{Math.round(item.weight * 100)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {checks?.claims && (checks.claims.numbers.length > 0 || checks.claims.values.length > 0) && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {[...checks.claims.numbers, ...checks.claims.values].filter((claim) => claim.text).map((claim, index) => (
                    <Badge key={index} variant={claim.verified ? "success" : "critical"} className="font-mono">
                      {claim.text}
                    </Badge>
                  ))}
                </div>
              )}
            </section>

            <section className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">AI judge</h3>
                <Button size="xs" variant="outline" onClick={() => judge.mutate()} disabled={judge.isPending || answer.status !== "SUCCESS"}>
                  {judge.isPending ? <Loader2 className="animate-spin" /> : <Gavel />}
                  {answer.judge?.at ? "Judge again" : "Check with AI judge"}
                </Button>
              </div>
              {answer.judge?.at ? (
                <div className={cn("rounded-lg border p-3 text-sm", answer.judge.faithful ? "border-success/30 bg-success/[0.06]" : "border-critical/30 bg-critical/[0.06]")}>
                  <p className="font-medium">{answer.judge.faithful ? "Supported by the result" : "Has unsupported claims"}</p>
                  {answer.judge.explanation && <p className="mt-1 text-[13px] text-muted-foreground">{answer.judge.explanation}</p>}
                  {(answer.judge.unsupported_claims ?? []).length > 0 && (
                    <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[13px]">
                      {answer.judge.unsupported_claims!.map((claim, index) => (
                        <li key={index}>{claim}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">A free model reads the question, SQL, saved rows and answer, and lists any claim the rows do not support. It runs only when you ask.</p>
              )}
            </section>

            <section className="space-y-2">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Saved result rows</h3>
              <PreviewTable columns={answer.preview?.columns ?? []} rows={answer.preview?.rows ?? []} />
              {answer.row_count !== null && <p className="text-[11px] text-muted-foreground">{formatNumber(answer.row_count)} row(s) in the full result.</p>}
            </section>

            <section className="space-y-2">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Your feedback</h3>
              <Textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="What was wrong or right about this answer? (optional)" rows={2} />
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant={answer.rating > 0 ? "default" : "outline"} onClick={() => feedback.mutate(1)} disabled={feedback.isPending}>
                  <ThumbsUp /> Good answer
                </Button>
                <Button size="sm" variant={answer.rating < 0 ? "destructive" : "outline"} onClick={() => feedback.mutate(-1)} disabled={feedback.isPending}>
                  <ThumbsDown /> Bad answer
                </Button>
                <span className="mx-1 w-px self-stretch bg-border" />
                {(["fixed", "dismissed", "open"] as const).map((state) => (
                  <Button key={state} size="sm" variant="ghost" onClick={() => review.mutate(state)} disabled={review.isPending || answer.review === state}>
                    {state === "fixed" ? "Mark fixed" : state === "dismissed" ? "Dismiss" : "Reopen"}
                  </Button>
                ))}
              </div>
            </section>

            <section className="space-y-2 pb-6">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Fix the SQL and add a golden test</h3>
              <p className="text-xs text-muted-foreground">
                Correct the SQL if needed, test it on your database, then promote it. The question and SQL join your feedback test set, and every later evaluation run checks it.
              </p>
              {answer.sql && <CodeBlock code={answer.sql} label="SQL used" />}
              <Textarea value={sql} onChange={(event) => setSql(event.target.value)} rows={5} className="font-mono text-xs" spellCheck={false} />
              {!connection ? (
                <p className="text-xs text-warning">
                  Connect a database first (<Link href="/connect" className="underline">Database</Link>) to test or promote SQL.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => testSql.mutate()} disabled={!sql.trim() || testSql.isPending}>
                    {testSql.isPending ? <Loader2 className="animate-spin" /> : <Play />} Test SQL
                  </Button>
                  <Button size="sm" onClick={() => promote.mutate()} disabled={!sql.trim() || promote.isPending || answer.review === "promoted"}>
                    {promote.isPending ? <Loader2 className="animate-spin" /> : <FlaskConical />}
                    {answer.review === "promoted" ? "Promoted" : "Promote to golden test"}
                  </Button>
                </div>
              )}
              {test && (
                <div className="space-y-2">
                  <p className="text-xs">
                    {formatNumber(test.row_count)} row(s).{" "}
                    {test.same_as_answer === null ? null : test.same_as_answer ? (
                      <span className="text-success">Same rows as the answer used.</span>
                    ) : (
                      <span className="text-warning">Different rows from the answer.</span>
                    )}
                  </p>
                  <PreviewTable columns={test.columns} rows={test.rows.slice(0, 20)} />
                </div>
              )}
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** A compact list of answers that opens the detail sheet. */
export function AnswerList({ rows, onOpen, empty }: { rows: AnswerCheck[]; onOpen: (id: string) => void; empty?: React.ReactNode }) {
  if (!rows.length) return <>{empty}</>;
  return (
    <ul className="divide-y overflow-hidden rounded-xl border bg-card">
      {rows.map((row) => (
        <li key={row.id}>
          <button type="button" onClick={() => onOpen(row.id)} className="flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/50">
            <span className={cn("size-2 shrink-0 rounded-full", row.risk === "high" ? "bg-critical" : row.risk === "medium" ? "bg-warning" : row.risk === "low" ? "bg-info" : "bg-success")} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{row.question}</p>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                {formatDateTime(row.ts)}
                {row.checks?.findings?.length ? ` · ${row.checks.findings.map((f) => findingLabel(f.kind)).slice(0, 2).join(", ")}` : ""}
              </p>
            </div>
            <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
              {row.rating > 0 && <ThumbsUp className="size-3.5 text-success" />}
              {row.rating < 0 && <ThumbsDown className="size-3.5 text-critical" />}
              <LevelBadge level={row.difficulty} />
              <RiskBadge risk={row.risk} />
            </div>
            <span className="tabular w-12 shrink-0 text-right text-xs text-muted-foreground">{row.faithfulness === null ? "-" : pct(row.faithfulness)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
