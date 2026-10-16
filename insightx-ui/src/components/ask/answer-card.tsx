"use client";

import * as React from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock,
  Code2,
  CornerDownRight,
  Database,
  HelpCircle,
  Rows3,
  SearchCheck,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { AutoChart, canChart } from "@/components/charts/auto-chart";
import { CodeBlock } from "@/components/common/code-block";
import { DataTable } from "@/components/common/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TrustBadge, TrustReasons } from "@/components/ask/trust-badge";
import { formatNumber, formatSeconds, stripEmoji } from "@/lib/format";
import type { QueryResponse } from "@/lib/types";

/** Plain text with paragraphs and simple "- " bullet lines. */
export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks = stripEmoji(text).split(/\n{2,}/);
  return (
    <div className={className}>
      {blocks.map((block, index) => {
        const lines = block.split("\n").filter((line) => line.trim());
        if (lines.length > 0 && lines.every((line) => /^\s*([-*•]|\d+[.)])\s+/.test(line))) {
          return (
            <ul key={index} className="my-2 list-disc space-y-1 pl-5">
              {lines.map((line, i) => (
                <li key={i}>{line.replace(/^\s*([-*•]|\d+[.)])\s+/, "").replace(/\*\*/g, "")}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={index} className="my-2 whitespace-pre-line first:mt-0 last:mb-0">
            {block.replace(/\*\*/g, "")}
          </p>
        );
      })}
    </div>
  );
}

export function FollowUps({ items, onAsk, disabled }: { items: string[]; onAsk: (question: string) => void; disabled?: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">Ask next</p>
      <div className="flex flex-wrap gap-2">
        {items.slice(0, 4).map((item) => (
          <button
            key={item}
            type="button"
            disabled={disabled}
            onClick={() => onAsk(item)}
            className="group inline-flex max-w-full items-center gap-1.5 rounded-full border bg-surface px-3 py-1.5 text-left text-[13px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
          >
            <CornerDownRight className="size-3.5 shrink-0 text-primary/70" />
            <span className="truncate">{stripEmoji(item)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

const NO_COLUMNS: string[] = [];
const NO_ROWS: QueryResponse["execution_result"]["rows"] = [];

function Meta({ result }: { result: QueryResponse }) {
  const exec = result.execution_result;
  const trace = result.trace ?? {};
  const cacheHit = typeof result.cache?.status === "string" && /hit|similar/i.test(result.cache.status);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {exec?.success && (
        <span className="inline-flex items-center gap-1.5">
          <Rows3 className="size-3.5" />
          {formatNumber(exec.row_count)} row{exec.row_count === 1 ? "" : "s"}
          {exec.truncated ? " (first part shown)" : ""}
        </span>
      )}
      {typeof trace.seconds === "number" && (
        <span className="inline-flex items-center gap-1.5">
          <Clock className="size-3.5" /> {formatSeconds(trace.seconds)}
        </span>
      )}
      {typeof trace.llm_calls === "number" && (
        <span className="inline-flex items-center gap-1.5">
          <Zap className="size-3.5" /> {trace.llm_calls} AI call{trace.llm_calls === 1 ? "" : "s"}
        </span>
      )}
      {cacheHit && (
        <Badge variant="info">
          <Database /> From cache
        </Badge>
      )}
      {(result.correction_attempts ?? 0) > 0 && <Badge variant="outline">SQL fixed {result.correction_attempts}x</Badge>}
      {trace.id && (
        <Link href={`/traces?id=${encodeURIComponent(trace.id)}`} className="inline-flex items-center gap-1.5 hover:text-foreground">
          <Activity className="size-3.5" /> Trace
        </Link>
      )}
    </div>
  );
}

export function AnswerCard({
  result,
  onAsk,
  onInvestigate,
  busy,
}: {
  result: QueryResponse;
  onAsk: (question: string) => void;
  onInvestigate: (question: string) => void;
  busy?: boolean;
}) {
  const exec = result.execution_result;
  const columns = exec?.columns ?? NO_COLUMNS;
  const rows = exec?.rows ?? NO_ROWS;
  const chartable = React.useMemo(() => canChart(columns, rows), [columns, rows]);
  const [tab, setTab] = React.useState(chartable ? "chart" : rows.length ? "table" : "sql");

  if (result.status === "CLARIFICATION") {
    return (
      <div className="rounded-xl border border-info/25 bg-info/[0.06] p-4">
        <div className="flex gap-3">
          <HelpCircle className="mt-0.5 size-4 shrink-0 text-info" />
          <div className="space-y-1">
            <p className="text-sm font-medium">I need one detail first</p>
            <RichText text={result.clarification || result.answer} className="text-sm text-muted-foreground" />
            <p className="pt-1 text-xs text-muted-foreground">Reply below and I will continue.</p>
          </div>
        </div>
      </div>
    );
  }

  if (result.status === "ERROR" || exec?.success === false && !result.answer) {
    return (
      <div className="rounded-xl border border-critical/25 bg-critical/[0.05] p-4">
        <div className="flex gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-critical" />
          <div className="min-w-0 space-y-2">
            <p className="text-sm font-medium">This question could not be answered</p>
            <p className="text-sm text-muted-foreground">{result.error || result.answer || exec?.error || "The backend returned an error."}</p>
            {result.error_stage && <Badge variant="outline">Failed at: {result.error_stage.replace(/_/g, " ")}</Badge>}
            {result.sql && <CodeBlock code={result.sql} className="mt-2" />}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <RichText text={result.answer || "Done. See the result below."} className="text-[15px] leading-relaxed" />
        <div className="flex flex-wrap items-center gap-3">
          <TrustBadge trust={result.trust} />
          <Meta result={result} />
        </div>
      </div>

      {(rows.length > 0 || result.sql) && (
        <div className="overflow-hidden rounded-xl border bg-card">
          <Tabs value={tab} onValueChange={setTab}>
            <div className="flex items-center justify-between gap-2 border-b px-3">
              <TabsList className="h-10 border-0">
                {chartable && (
                  <TabsTrigger value="chart">
                    <BarChart3 className="size-3.5" /> Chart
                  </TabsTrigger>
                )}
                {rows.length > 0 && (
                  <TabsTrigger value="table">
                    <Rows3 className="size-3.5" /> Table
                  </TabsTrigger>
                )}
                {result.sql && (
                  <TabsTrigger value="sql">
                    <Code2 className="size-3.5" /> SQL
                  </TabsTrigger>
                )}
                {result.trust?.reasons?.length ? (
                  <TabsTrigger value="trust">
                    <ShieldCheck className="size-3.5" /> Checks
                  </TabsTrigger>
                ) : null}
              </TabsList>
              <Button variant="ghost" size="xs" disabled={busy} onClick={() => onInvestigate(result.question)} className="text-muted-foreground">
                <SearchCheck /> Find out why
              </Button>
            </div>
            {chartable && (
              <TabsContent value="chart" className="p-4">
                <AutoChart columns={columns} rows={rows} preferred={result.chart_data?.kind} />
              </TabsContent>
            )}
            {rows.length > 0 && (
              <TabsContent value="table" className="p-0">
                <DataTable columns={columns} rows={rows} pageSize={10} className="rounded-none border-0" exportName="insightx-answer.csv" />
              </TabsContent>
            )}
            {result.sql && (
              <TabsContent value="sql" className="p-4">
                <CodeBlock code={result.sql} />
              </TabsContent>
            )}
            {result.trust?.reasons?.length ? (
              <TabsContent value="trust" className="p-4">
                <TrustReasons trust={result.trust} />
              </TabsContent>
            ) : null}
          </Tabs>
        </div>
      )}

      <FollowUps items={result.follow_ups ?? []} onAsk={onAsk} disabled={busy} />
    </div>
  );
}
