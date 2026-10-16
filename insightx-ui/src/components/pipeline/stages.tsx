"use client";

import * as React from "react";
import {
  ArrowRight,
  Boxes,
  Braces,
  CircleCheck,
  DatabaseZap,
  FileCheck2,
  GitFork,
  Link2,
  ListTree,
  MessageSquareQuote,
  Network,
  PenLine,
  Play,
  ShieldCheck,
  Sparkles,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { CodeBlock } from "@/components/common/code-block";
import { DataTable } from "@/components/common/data-table";
import { TrustBadge, TrustReasons } from "@/components/ask/trust-badge";
import { CheckView, checkPassed } from "@/components/pipeline/check-view";
import { ValueView, isEmpty } from "@/components/pipeline/value-view";
import type { Json, PipelineResult } from "@/components/pipeline/pipeline-types";
import { Badge } from "@/components/ui/badge";
import { formatNumber, humanize, stripEmoji, truncate } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface StageDef {
  key: string;
  title: string;
  /** What the step does, in one plain sentence. */
  about: string;
  icon: LucideIcon;
  /** Step names the backend records in the run's trace. */
  steps: string[];
  ran: (result: PipelineResult) => boolean;
  summary: (result: PipelineResult) => string;
  failed?: (result: PipelineResult) => boolean;
  Body: (props: { result: PipelineResult }) => React.ReactNode;
}

function text(value: Json | undefined): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))) : [];
}

function Chips({ items, tone = "default" }: { items: string[]; tone?: "default" | "primary" | "info" }) {
  if (items.length === 0) return <span className="text-[13px] text-muted-foreground">None</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <span
          key={item}
          className={cn(
            "rounded-md border px-1.5 py-0.5 font-mono text-[11.5px]",
            tone === "primary" ? "border-primary/30 bg-primary/10 text-primary" : tone === "info" ? "border-info/30 bg-info/10 text-info" : "bg-muted/50",
          )}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function Raw({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = React.useState(false);
  if (isEmpty(value)) return null;
  return (
    <div>
      <button type="button" onClick={() => setOpen((current) => !current)} aria-expanded={open} className="cursor-pointer text-xs text-primary underline-offset-4 hover:underline">
        {open ? `Hide ${label}` : `Show ${label}`}
      </button>
      {open && (
        <pre className="scrollbar-thin mt-2 max-h-80 overflow-auto rounded-lg border bg-background/60 p-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
          {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- bodies

function CacheBody({ result }: { result: PipelineResult }) {
  const cache = result.cache ?? {};
  const hit = Boolean(cache.hit) || /hit|reuse|answer|sql/i.test(String(cache.status ?? ""));
  return (
    <div className="space-y-3 text-[13px]">
      <p>{hit ? "An earlier, matching question was found, so its saved work was reused." : "No saved answer matched, so every step below ran fresh."}</p>
      {!isEmpty(cache) && <ValueView value={cache} />}
    </div>
  );
}

function RewriteBody({ result }: { result: PipelineResult }) {
  const variants = (result.query_variants ?? []).slice(1);
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr] md:items-stretch">
        <div className="rounded-lg border bg-background/40 p-3">
          <p className="mb-1 text-xs font-medium text-muted-foreground">You asked</p>
          <p className="text-[13px]">{result.question}</p>
        </div>
        <ArrowRight aria-hidden className="mx-auto hidden size-4 self-center text-muted-foreground md:block" />
        <div className="rounded-lg border border-primary/30 bg-primary/5 p-3">
          <p className="mb-1 text-xs font-medium text-primary">Rewritten for the database</p>
          <p className="text-[13px]">{text(result.rewritten_query) || "Not available"}</p>
        </div>
      </div>
      {variants.length > 0 && (
        <Field label="Other phrasings used to search the schema">
          <ul className="list-disc space-y-1 pl-4 text-[13px]">
            {variants.map((variant) => (
              <li key={variant}>{variant}</li>
            ))}
          </ul>
        </Field>
      )}
    </div>
  );
}

function DecomposeBody({ result }: { result: PipelineResult }) {
  const value = result.decomposition;
  if (typeof value === "string") return <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{value}</p>;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return (
      <ol className="space-y-2">
        {value.map((item, index) => (
          <li key={index} className="flex gap-3 text-[13px]">
            <span className="tabular flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">{index + 1}</span>
            <span>{String(item)}</span>
          </li>
        ))}
      </ol>
    );
  }
  return <ValueView value={value} />;
}

interface Ranked {
  table: string;
  relevance: number;
  keyword?: number;
  semantic?: number;
  columns?: number;
  value_hint?: boolean;
  named?: boolean;
}

function num(value: Json | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function ranking(result: PipelineResult): Ranked[] {
  const raw = result.retrieved_schema;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item): Ranked | null => {
      if (typeof item === "string") return { table: item, relevance: 0 };
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const row = item as Record<string, Json>;
        const table = String(row.table ?? row.table_name ?? row.name ?? "");
        return {
          table,
          relevance: num(row.hybrid_score) ?? num(row.relevance) ?? num(row.score) ?? 0,
          keyword: num(row.keyword_score),
          semantic: num(row.semantic_score),
          columns: Array.isArray(row.columns) ? row.columns.length : undefined,
          value_hint: Boolean(row.value_hint),
          named: Boolean(row.named),
        };
      }
      return null;
    })
    .filter((item): item is Ranked => !!item && !!item.table);
}

function RetrieveBody({ result }: { result: PipelineResult }) {
  const rows = ranking(result);
  if (rows.length === 0) return <ValueView value={result.retrieved_schema} />;
  const top = Math.max(...rows.map((row) => row.relevance), 0.0001);
  const picked = new Set(list(result.graph_context?.retrieved_tables));
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-muted-foreground">Tables ranked by how well they match your question. The score mixes keyword search (K) and meaning search (M).</p>
      <ul className="space-y-1.5">
        {rows.slice(0, 15).map((row, index) => (
          <li
            key={row.table}
            title={[row.keyword !== undefined ? `keyword ${row.keyword.toFixed(3)}` : "", row.semantic !== undefined ? `meaning ${row.semantic.toFixed(3)}` : "", row.columns !== undefined ? `${row.columns} columns` : ""].filter(Boolean).join(", ")}
            className="grid grid-cols-[1.5rem_minmax(0,11rem)_1fr_auto] items-center gap-3 text-[13px]"
          >
            <span className="tabular text-right text-xs text-muted-foreground">{index + 1}</span>
            <span className={cn("truncate font-mono text-[12px]", picked.has(row.table) && "text-primary")}>{row.table}</span>
            <span className="h-2 overflow-hidden rounded-full bg-muted">
              <span
                className={cn("block h-full rounded-full", picked.has(row.table) ? "bg-[linear-gradient(90deg,var(--chart-1),var(--chart-2))]" : "bg-muted-foreground/40")}
                style={{ width: `${Math.max(4, (row.relevance / top) * 100)}%` }}
              />
            </span>
            <span className="flex items-center gap-1.5">
              <span className="tabular w-12 text-right text-xs text-muted-foreground">{row.relevance.toFixed(3)}</span>
              {row.keyword !== undefined && row.semantic !== undefined && (
                <span className="tabular hidden text-[11px] text-muted-foreground xl:inline">
                  K {row.keyword.toFixed(2)} / M {row.semantic.toFixed(2)}
                </span>
              )}
              {row.value_hint && <Badge variant="info">value match</Badge>}
              {row.named && <Badge variant="primary">named</Badge>}
            </span>
          </li>
        ))}
      </ul>
      {rows.length > 15 && <p className="text-xs text-muted-foreground">And {rows.length - 15} more tables with lower scores.</p>}
    </div>
  );
}

function GraphBody({ result }: { result: PipelineResult }) {
  const context = result.graph_context ?? {};
  const retrieved = list(context.retrieved_tables);
  const connectors = list(context.connector_tables);
  const relevant = list(context.relevant_tables);
  const joins = list(context.join_paths);
  const unreachable = list(context.unreachable_tables);
  const known = new Set(["retrieved_tables", "connector_tables", "relevant_tables", "expanded_tables", "join_paths", "unreachable_tables", "relationships", "value_hints", "fallback", "table_relevance", "total_tables", "dropped_tables"]);
  const rest = Object.fromEntries(Object.entries(context).filter(([key, value]) => !known.has(key) && !isEmpty(value)));
  const total = typeof context.total_tables === "number" ? context.total_tables : null;
  const dropped = list(context.dropped_tables);
  return (
    <div className="space-y-4">
      {context.fallback ? (
        <p className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[13px] text-warning">
          No clear match was found, so the whole schema was used ({humanize(String(context.fallback))}).
        </p>
      ) : null}
      {total !== null && (
        <p className="text-[13px]">
          Kept <span className="font-semibold">{relevant.length || retrieved.length}</span> of {total} tables in your database for the next step.
        </p>
      )}
      <JoinGraph retrieved={retrieved} connectors={connectors} joins={joins} />
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Matched tables">
          <Chips items={retrieved} tone="primary" />
        </Field>
        <Field label="Bridge tables added to join them">
          <Chips items={connectors} tone="info" />
        </Field>
      </div>
      {relevant.length > 0 && (
        <Field label="All tables sent to the next step">
          <Chips items={relevant} />
        </Field>
      )}
      {joins.length > 0 && (
        <Field label="Join paths">
          <ul className="space-y-1">
            {joins.map((join) => (
              <li key={join} className="flex items-center gap-2 font-mono text-[12px]">
                <Link2 className="size-3.5 shrink-0 text-muted-foreground" /> {join}
              </li>
            ))}
          </ul>
        </Field>
      )}
      {!isEmpty(context.value_hints) && (
        <Field label="Values from your question found in the data">
          <ValueView value={context.value_hints} />
        </Field>
      )}
      {dropped.length > 0 && (
        <Field label="Tables dropped as not relevant">
          <Chips items={dropped} />
        </Field>
      )}
      {unreachable.length > 0 && (
        <Field label="Tables that could not be joined">
          <Chips items={unreachable} />
        </Field>
      )}
      {!isEmpty(rest) && <ValueView value={rest} />}
      <Raw label="relationships" value={context.relationships} />
    </div>
  );
}

/** A small node-link drawing of the tables and the joins between them. */
function JoinGraph({ retrieved, connectors, joins }: { retrieved: string[]; connectors: string[]; joins: string[] }) {
  const nodes = Array.from(new Set([...retrieved, ...connectors])).slice(0, 10);
  if (nodes.length < 2) return null;
  const width = 640;
  const height = 220;
  const cx = width / 2;
  const cy = height / 2;
  const rx = width / 2 - 90;
  const ry = height / 2 - 34;
  const position = new Map(nodes.map((name, index) => {
    const angle = (index / nodes.length) * Math.PI * 2 + (nodes.length === 2 ? Math.PI : -Math.PI / 2);
    return [name, { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) }] as const;
  }));
  const edges: [string, string][] = [];
  for (const join of joins) {
    const tables = Array.from(join.matchAll(/([A-Za-z_][\w$]*)\.[A-Za-z_][\w$]*/g)).map((match) => match[1] ?? "");
    for (let index = 0; index + 1 < tables.length; index += 1) {
      const a = tables[index] ?? "";
      const b = tables[index + 1] ?? "";
      if (a !== b && position.has(a) && position.has(b)) edges.push([a, b]);
    }
  }
  const connectorSet = new Set(connectors);
  return (
    <div className="overflow-x-auto rounded-lg border bg-background/40">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Join map of ${nodes.length} tables`} className="mx-auto block h-auto w-full min-w-[480px] max-w-[640px]">
        {edges.map(([a, b], index) => {
          const from = position.get(a)!;
          const to = position.get(b)!;
          return <line key={index} x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="var(--primary)" strokeOpacity="0.55" strokeWidth="1.5" strokeDasharray="4 3" />;
        })}
        {nodes.map((name) => {
          const point = position.get(name)!;
          const label = truncate(name, 18);
          const boxWidth = Math.max(70, label.length * 7.2 + 20);
          const bridge = connectorSet.has(name);
          return (
            <g key={name} transform={`translate(${point.x - boxWidth / 2}, ${point.y - 13})`}>
              <rect width={boxWidth} height="26" rx="7" fill="var(--card)" stroke={bridge ? "var(--info)" : "var(--primary)"} strokeOpacity="0.7" />
              <text x={boxWidth / 2} y="17" textAnchor="middle" fontSize="11.5" fontFamily="var(--font-mono)" fill="var(--foreground)">
                {label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function LinkBody({ result }: { result: PipelineResult }) {
  const linked = result.linked_schema ?? {};
  const corrections = list(linked.corrections);
  const issues = list(linked.issues);
  const tables = list(linked.tables);
  const joins = list(linked.joins);
  const known = new Set(["status", "tables", "joins", "corrections", "issues", "raw"]);
  const rest = Object.fromEntries(Object.entries(linked).filter(([key, value]) => !known.has(key) && !isEmpty(value)));
  return (
    <div className="space-y-4">
      {linked.status ? (
        <p className="text-[13px]">
          Status: <span className="font-medium">{humanize(String(linked.status))}</span>
          <span className="text-muted-foreground">
            {" "}
            ({tables.length} {tables.length === 1 ? "table" : "tables"}, {joins.length} {joins.length === 1 ? "join" : "joins"})
          </span>
        </p>
      ) : null}
      {tables.length > 0 && (
        <Field label="Tables used">
          <Chips items={tables} tone="primary" />
        </Field>
      )}
      {joins.length > 0 && (
        <Field label="Joins">
          <ul className="space-y-1 font-mono text-[12px]">
            {joins.map((join) => (
              <li key={join}>{join}</li>
            ))}
          </ul>
        </Field>
      )}
      {corrections.length > 0 && (
        <Field label="Fixed by verification">
          <ul className="space-y-1 text-[13px]">
            {corrections.map((item) => (
              <li key={item} className="flex gap-2 text-success">
                <CircleCheck className="mt-0.5 size-3.5 shrink-0" /> {item}
              </li>
            ))}
          </ul>
        </Field>
      )}
      {issues.length > 0 && (
        <Field label="Problems found">
          <ul className="list-disc space-y-1 pl-4 text-[13px] text-warning">
            {issues.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Field>
      )}
      {!isEmpty(rest) && <ValueView value={rest} />}
      <Raw label="schema sent to the SQL writer" value={result.schema_linking} />
      <Raw label="raw linker reply" value={linked.raw} />
    </div>
  );
}

function SqlBody({ result }: { result: PipelineResult }) {
  const candidates = result.sql_candidates ?? [];
  const selection = result.sql_selection ?? {};
  const agreement = typeof selection.agreement === "number" ? selection.agreement : null;
  if (candidates.length <= 1) {
    const sql = result.generated_sql || result.sql;
    return sql ? <CodeBlock code={sql} /> : <p className="text-[13px] text-muted-foreground">No SQL was written for this question.</p>;
  }
  return (
    <div className="space-y-4">
      <p className="text-[13px]">
        {candidates.length} versions of the SQL were written and run side by side.
        {agreement !== null && <span className="text-muted-foreground"> {Math.round(agreement * 100)}% of the ones that ran gave the same result.</span>}
      </p>
      {selection.reason ? <p className="rounded-lg border bg-background/40 px-3 py-2 text-[13px] text-muted-foreground">{String(selection.reason)}</p> : null}
      <div className="space-y-3">
        {candidates.map((candidate) => {
          const ok = /^(ok|success)/i.test(String(candidate.status ?? "ok"));
          return (
            <div key={candidate.index} className={cn("rounded-lg border p-3", candidate.selected && "border-primary/40 bg-primary/5 fx-glow")}>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px]">
                <span className="font-medium">Version {candidate.index + 1}</span>
                <Badge variant={ok ? "success" : "critical"}>{humanize(String(candidate.status ?? "ok"))}</Badge>
                {candidate.row_count !== undefined && <Badge variant="outline">{formatNumber(candidate.row_count)} rows</Badge>}
                {candidate.cluster !== undefined && <Badge variant="outline">group {Number(candidate.cluster) + 1}</Badge>}
                {candidate.selected && <Badge variant="primary">Chosen</Badge>}
              </div>
              {candidate.error && <p className="mb-2 text-xs text-critical">{String(candidate.error)}</p>}
              <CodeBlock code={candidate.sql} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CorrectionBody({ result }: { result: PipelineResult }) {
  const attempts = result.correction_attempts ?? 0;
  if (!attempts && !result.corrected_sql) return <p className="text-[13px] text-muted-foreground">The SQL passed its checks, so no fix was needed.</p>;
  return (
    <div className="space-y-3">
      <p className="text-[13px]">
        The SQL was repaired {attempts} {attempts === 1 ? "time" : "times"} after a check or the database reported a problem.
      </p>
      {result.corrected_sql && <CodeBlock code={result.corrected_sql} label="Fixed SQL" />}
    </div>
  );
}

function ExecuteBody({ result }: { result: PipelineResult }) {
  const execution = result.execution_result;
  if (!execution || isEmpty(execution)) return <p className="text-[13px] text-muted-foreground">The query was not run.</p>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-[13px]">
        <Badge variant={execution.success ? "success" : "critical"}>{execution.success ? "Ran" : "Failed"}</Badge>
        <Badge variant="outline">{formatNumber(execution.row_count)} rows</Badge>
        {execution.execution_time_ms ? <Badge variant="outline">{formatNumber(execution.execution_time_ms)} ms in the database</Badge> : null}
        {execution.truncated && <Badge variant="warning">Showing the first {formatNumber(execution.max_rows)} rows</Badge>}
      </div>
      {execution.error && <p className="text-[13px] text-critical">{execution.error}</p>}
      {execution.rows?.length > 0 && <DataTable columns={execution.columns ?? []} rows={execution.rows} pageSize={5} exportName="pipeline-result.csv" />}
    </div>
  );
}

function AnswerBody({ result }: { result: PipelineResult }) {
  const answer = stripEmoji(result.answer || result.clarification || result.error || "");
  return (
    <div className="space-y-4">
      {answer ? <p className="rounded-lg border bg-background/40 p-3 text-[14px] leading-relaxed whitespace-pre-wrap">{answer}</p> : <p className="text-[13px] text-muted-foreground">No answer text was produced.</p>}
      {result.trust && !isEmpty(result.trust) && (
        <Field label="How far you can trust it">
          <div className="space-y-3">
            <TrustBadge trust={result.trust} />
            <TrustReasons trust={result.trust} />
          </div>
        </Field>
      )}
      {!isEmpty(result.claim_check) && (
        <Field label="Numbers in the answer checked against the result">
          <ValueView value={result.claim_check} />
        </Field>
      )}
      {result.follow_ups?.length > 0 && (
        <Field label="Suggested next questions">
          <Chips items={result.follow_ups} />
        </Field>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- stages

export const STAGES: StageDef[] = [
  {
    key: "cache",
    title: "Saved answer check",
    about: "Looks for an earlier question with the same meaning so work is not repeated.",
    icon: DatabaseZap,
    steps: ["cache_check", "cache_check_rewritten"],
    ran: (r) => !isEmpty(r.cache),
    summary: (r) => (r.cache?.hit || /hit|reuse/i.test(String(r.cache?.status ?? "")) ? "Reused saved work" : "Nothing saved, ran fresh"),
    Body: CacheBody,
  },
  {
    key: "rewrite",
    title: "Query rewriting",
    about: "Turns your words into a clear, complete question the database can answer.",
    icon: PenLine,
    steps: ["rewrite"],
    ran: (r) => !isEmpty(r.rewritten_query),
    summary: (r) => truncate(text(r.rewritten_query), 90),
    Body: RewriteBody,
  },
  {
    key: "decompose",
    title: "Query decomposition",
    about: "Splits the question into the smaller parts the SQL has to cover.",
    icon: ListTree,
    steps: ["decompose"],
    ran: (r) => !isEmpty(r.decomposition),
    summary: (r) => (Array.isArray(r.decomposition) ? `${r.decomposition.length} parts` : truncate(text(r.decomposition), 90)),
    Body: DecomposeBody,
  },
  {
    key: "retrieve",
    title: "Hybrid retrieval",
    about: "Searches every table by keywords and meaning to find the ones that matter.",
    icon: Boxes,
    steps: ["retrieve"],
    ran: (r) => !isEmpty(r.retrieved_schema),
    summary: (r) => {
      const rows = ranking(r);
      return rows.length ? `${rows.length} tables ranked, top: ${rows[0]?.table}` : "Schema searched";
    },
    Body: RetrieveBody,
  },
  {
    key: "graph",
    title: "GraphRAG",
    about: "Follows the links between tables to add any bridge tables needed for joins.",
    icon: Network,
    steps: [],
    ran: (r) => !isEmpty(r.graph_context),
    summary: (r) => {
      const matched = list(r.graph_context?.retrieved_tables).length;
      const bridges = list(r.graph_context?.connector_tables).length;
      const joins = list(r.graph_context?.join_paths).length;
      return `${matched} matched, ${bridges} bridge, ${joins} join paths`;
    },
    Body: GraphBody,
  },
  {
    key: "link",
    title: "Schema linking",
    about: "Picks the exact tables, columns and joins, then double-checks them.",
    icon: GitFork,
    steps: ["schema_link"],
    ran: (r) => !isEmpty(r.linked_schema) || !isEmpty(r.schema_linking),
    summary: (r) => {
      const tables = list(r.linked_schema?.tables);
      return tables.length ? `Uses ${tables.join(", ")}` : "Columns and joins chosen";
    },
    Body: LinkBody,
  },
  {
    key: "sql",
    title: "SQL generation",
    about: "Writes the SQL, sometimes several versions, and keeps the one the others agree with.",
    icon: Braces,
    steps: ["generate_sql", "select_sql"],
    ran: (r) => !!(r.generated_sql || r.sql) || (r.sql_candidates?.length ?? 0) > 0,
    summary: (r) => ((r.sql_candidates?.length ?? 0) > 1 ? `${r.sql_candidates?.length} versions written, best one chosen` : "SQL written"),
    Body: SqlBody,
  },
  {
    key: "safety",
    title: "SQL safety",
    about: "Makes sure the SQL only reads data and cannot change or delete anything.",
    icon: ShieldCheck,
    steps: ["safety"],
    ran: (r) => !isEmpty(r.safety_result),
    failed: (r) => checkPassed(r.safety_result, "safe") === false,
    summary: (r) => (checkPassed(r.safety_result, "safe") === false ? "Blocked" : "Read-only, safe to run"),
    Body: ({ result }) => <CheckView check={result.safety_result} passKey="safe" />,
  },
  {
    key: "validate",
    title: "SQL validation",
    about: "Checks the SQL against the real schema before it runs.",
    icon: FileCheck2,
    steps: ["validate_sql"],
    ran: (r) => !isEmpty(r.validation_result),
    failed: (r) => checkPassed(r.validation_result) === false,
    summary: (r) => {
      const warnings = r.validation_result?.warnings?.length ?? 0;
      return checkPassed(r.validation_result) === false ? "Problems found" : warnings ? `Valid, ${warnings} warnings` : "Valid";
    },
    Body: ({ result }) => <CheckView check={result.validation_result} />,
  },
  {
    key: "correct",
    title: "SQL correction",
    about: "Repairs the SQL automatically if a check or the database rejects it.",
    icon: Wrench,
    steps: ["correction"],
    ran: (r) => (r.correction_attempts ?? 0) > 0 || !!r.corrected_sql,
    summary: (r) => ((r.correction_attempts ?? 0) > 0 ? `Fixed ${r.correction_attempts} times` : "Not needed"),
    Body: CorrectionBody,
  },
  {
    key: "execute",
    title: "Query execution",
    about: "Runs the SQL on your database in read-only mode.",
    icon: Play,
    steps: ["execute"],
    ran: (r) => !isEmpty(r.execution_result) && (r.execution_result.success || !!r.execution_result.error || r.execution_result.row_count > 0),
    failed: (r) => !!r.execution_result && !r.execution_result.success && !!r.execution_result.error,
    summary: (r) => (r.execution_result?.success ? `${formatNumber(r.execution_result.row_count)} rows returned` : "Did not run"),
    Body: ExecuteBody,
  },
  {
    key: "result",
    title: "Result validation",
    about: "Checks the returned rows make sense for the question (not empty, no odd values).",
    icon: Sparkles,
    steps: ["validate_result"],
    ran: (r) => !isEmpty(r.result_validation),
    failed: (r) => checkPassed(r.result_validation) === false,
    summary: (r) => (checkPassed(r.result_validation) === false ? "Problems found" : "Looks right"),
    Body: ({ result }) => <CheckView check={result.result_validation} />,
  },
  {
    key: "answer",
    title: "Answer and trust",
    about: "Writes the answer in plain English, checks every number in it, and scores trust.",
    icon: MessageSquareQuote,
    steps: ["generate_answer", "clarification"],
    ran: (r) => !!(r.answer || r.clarification),
    summary: (r) => truncate(stripEmoji(r.answer || r.clarification || ""), 90) || "No answer",
    Body: AnswerBody,
  },
];
