"use client";

// Lively pieces for the Ask page: animated "thinking" steps while an answer
// is on its way, a glowing frame around answers, and a big counting number
// when the answer is a single value.

import * as React from "react";
import { Check, Loader2 } from "lucide-react";
import { NumberTicker } from "@/components/fx/number-ticker";
import type { QueryResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const ANSWER_STEPS = ["Understanding your question", "Finding the right tables", "Writing the SQL", "Running and checking it", "Preparing the answer and chart"];
const WHY_STEPS = ["Understanding the change", "Comparing the two periods", "Testing possible causes", "Ranking the real reasons", "Writing the explanation"];
const STEP_MS = [900, 1800, 2600, 3200];

function useTick(ms: number) {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(timer);
  }, [ms]);
  return now;
}

/** Steps that light up one by one while InsightX works. The last step waits until the answer arrives. */
export function ThinkingSteps({ kind, since }: { kind: "answer" | "investigation"; since: number }) {
  const now = useTick(250);
  const elapsed = Math.max(0, now - since);
  const steps = kind === "investigation" ? WHY_STEPS : ANSWER_STEPS;
  let current = 0;
  let total = 0;
  for (const ms of STEP_MS) {
    total += ms;
    if (elapsed >= total) current += 1;
  }
  current = Math.min(current, steps.length - 1);
  const progress = Math.min(94, ((current + Math.min(1, (elapsed % 2600) / 2600)) / steps.length) * 100);

  return (
    <div role="status" aria-live="polite" className="relative overflow-hidden rounded-2xl border border-primary/20 bg-card p-4 shadow-[0_10px_40px_-18px_var(--glow)] animate-in fade-in slide-in-from-bottom-2 duration-500">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[linear-gradient(110deg,transparent_30%,color-mix(in_oklch,var(--primary)_7%,transparent)_50%,transparent_70%)] bg-[length:250%_100%] [animation:fx-shimmer_2.2s_linear_infinite]" />
      <div className="relative flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm font-medium">
          <span className="relative flex size-2.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" />
            <span className="relative inline-flex size-2.5 rounded-full bg-primary" />
          </span>
          {kind === "investigation" ? "Investigating" : "Working on your answer"}
        </p>
        <span className="tabular text-xs text-muted-foreground">{Math.floor(elapsed / 1000)}s</span>
      </div>
      <ol className="relative mt-3 space-y-2">
        {steps.map((step, index) => {
          const done = index < current;
          const active = index === current;
          return (
            <li
              key={step}
              className={cn(
                "flex items-center gap-2.5 text-[13px] transition-all duration-500",
                done && "text-muted-foreground",
                active && "font-medium text-foreground",
                !done && !active && "text-muted-foreground/50",
              )}
            >
              <span
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors duration-500",
                  done && "border-success/40 bg-success/15 text-success",
                  active && "border-primary/50 bg-primary/10 text-primary",
                )}
              >
                {done ? <Check className="size-3" /> : active ? <Loader2 className="size-3 animate-spin" /> : <span className="size-1 rounded-full bg-current" />}
              </span>
              {step}
            </li>
          );
        })}
      </ol>
      <div className="relative mt-4 h-1 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-[linear-gradient(90deg,var(--primary),color-mix(in_oklch,var(--primary)_55%,var(--info)))] transition-[width] duration-700 ease-out" style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}

/** Soft glowing card that slides in around a finished answer. */
export function AnswerFrame({ children, tone = "primary" }: { children: React.ReactNode; tone?: "primary" | "critical" }) {
  return (
    <div
      className={cn(
        "relative rounded-2xl p-px animate-in fade-in slide-in-from-bottom-3 duration-500",
        tone === "primary"
          ? "bg-[linear-gradient(135deg,color-mix(in_oklch,var(--primary)_45%,transparent),var(--border)_40%,var(--border)_60%,color-mix(in_oklch,var(--info)_35%,transparent))]"
          : "bg-critical/25",
      )}
    >
      <div className="rounded-[calc(1rem-1px)] bg-card p-4 shadow-[0_18px_50px_-28px_var(--glow)] sm:p-5">{children}</div>
    </div>
  );
}

function isNumber(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string" && value.trim() !== "") return Number.isFinite(Number(value.replace(/,/g, "")));
  return false;
}

function prettyLabel(column: string): string {
  const text = column.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** When the answer is one row of numbers (like a total or a count), show them big. */
export function HeadlineNumbers({ result }: { result: QueryResponse }) {
  const exec = result.execution_result;
  const rows = exec?.rows ?? [];
  const columns = exec?.columns ?? [];
  if (rows.length !== 1 || columns.length === 0 || columns.length > 4) return null;
  const row = rows[0] as unknown;
  const valueOf = (column: string, index: number): unknown =>
    Array.isArray(row) ? row[index] : row && typeof row === "object" ? (row as Record<string, unknown>)[column] : undefined;
  const stats = columns.map((column, index) => ({ column, value: valueOf(column, index) })).filter((item) => isNumber(item.value));
  if (stats.length === 0) return null;

  return (
    <div className={cn("grid gap-3", stats.length > 1 && "sm:grid-cols-2")}>
      {stats.map((stat, index) => {
        const number = typeof stat.value === "number" ? stat.value : Number(String(stat.value).replace(/,/g, ""));
        const display = Number.isInteger(number) ? number.toLocaleString("en-US") : number.toLocaleString("en-US", { maximumFractionDigits: 2 });
        return (
          <div
            key={stat.column}
            className="relative overflow-hidden rounded-xl border bg-[linear-gradient(135deg,color-mix(in_oklch,var(--primary)_8%,var(--card)),var(--card))] px-4 py-3 animate-in fade-in zoom-in-95 duration-500 fill-mode-both"
            style={{ animationDelay: `${120 + index * 90}ms` }}
          >
            <p className="text-xs font-medium text-muted-foreground">{prettyLabel(stat.column)}</p>
            <p className="tabular mt-1 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              <NumberTicker value={display} duration={1100} />
            </p>
          </div>
        );
      })}
    </div>
  );
}
