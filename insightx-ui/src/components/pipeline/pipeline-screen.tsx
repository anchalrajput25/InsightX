"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowUp, Database, Loader2, MessageSquareText, Workflow, XCircle } from "lucide-react";
import { PageHeader } from "@/components/common/page-header";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { RunningView, RunView } from "@/components/pipeline/run-view";
import { loadAskRuns, loadRuns, saveRuns } from "@/components/pipeline/run-store";
import { STAGES } from "@/components/pipeline/stages";
import type { PipelineResult, Run } from "@/components/pipeline/pipeline-types";
import { useConnection } from "@/components/providers/connection-provider";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ApiError, errorMessage } from "@/lib/api/client";
import { questionsApi } from "@/lib/api/questions";
import { stripEmoji, timeAgo, truncate } from "@/lib/format";
import { newThreadId } from "@/lib/ui-prefs";
import { cn } from "@/lib/utils";

function RunButton({ run, active, onSelect }: { run: Run; active: boolean; onSelect: () => void }) {
  const status = run.state === "done" ? String(run.result.status).toUpperCase() : run.state;
  const dot = run.state === "running" ? "bg-primary animate-pulse" : run.state === "error" || (status !== "SUCCESS" && status !== "CLARIFICATION") ? "bg-critical" : status === "CLARIFICATION" ? "bg-warning" : "bg-success";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex w-full cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-accent/60",
        active && "bg-[linear-gradient(90deg,color-mix(in_oklch,var(--primary)_14%,transparent),transparent)] shadow-[inset_0_1px_0_var(--edge)]",
      )}
    >
      <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", dot)} aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2">{truncate(stripEmoji(run.question), 90)}</span>
        {run.source === "pipeline" && <span className="block text-[11px] text-muted-foreground">{run.state === "running" ? "Running…" : timeAgo(run.at)}</span>}
      </span>
    </button>
  );
}

function Overview() {
  return (
    <div className="space-y-5">
      <SpotlightCard className="relative isolate overflow-hidden rounded-2xl border bg-card p-6">
        <div aria-hidden className="pointer-events-none absolute -left-16 -top-24 -z-10 size-72 rounded-full bg-[var(--glow)] blur-3xl" />
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-primary/15 text-primary">
            <Workflow className="size-5" />
          </span>
          <div>
            <h2 className="text-base font-semibold">How InsightX answers a question</h2>
            <p className="text-[13px] text-muted-foreground">Ask something on the left, or open an answer from your Ask chats, to see each step with real data.</p>
          </div>
        </div>
      </SpotlightCard>
      <ol className="fx-stagger grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {STAGES.map((stage, index) => (
          <li key={stage.key}>
            <SpotlightCard className="h-full rounded-xl border bg-card p-4">
              <div className="flex items-center gap-2.5">
                <span className="flex size-8 items-center justify-center rounded-lg bg-primary/12 text-primary">
                  <stage.icon className="size-4" />
                </span>
                <span className="tabular text-xs text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
                <span className="text-sm font-semibold">{stage.title}</span>
              </div>
              <p className="mt-2 text-[13px] text-muted-foreground">{stage.about}</p>
            </SpotlightCard>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function PipelineScreen() {
  const { connection, ready, setConnection } = useConnection();
  const [runs, setRuns] = React.useState<Run[]>([]);
  const [askRuns, setAskRuns] = React.useState<Run[]>([]);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [text, setText] = React.useState("");
  const [useCache, setUseCache] = React.useState(true);
  const thread = React.useRef<string>("");

  React.useEffect(() => {
    setRuns(loadRuns());
    setAskRuns(loadAskRuns());
    thread.current = newThreadId();
  }, []);

  const update = React.useCallback((change: (current: Run[]) => Run[]) => {
    setRuns((current) => {
      const next = change(current);
      saveRuns(next);
      return next;
    });
  }, []);

  const busy = runs.some((run) => run.state === "running");

  const send = async () => {
    const question = text.trim();
    if (!question || busy || !connection) return;
    const id = `run-${Date.now()}`;
    const at = Date.now();
    update((current) => [{ id, question, source: "pipeline", at, state: "running" }, ...current]);
    setSelected(id);
    setText("");
    try {
      const result = (await questionsApi.ask(connection.id, question, thread.current || newThreadId(), useCache)) as PipelineResult;
      update((current) => current.map((run) => (run.id === id ? { id, question, source: "pipeline", at, state: "done", result } : run)));
    } catch (error) {
      update((current) => current.map((run) => (run.id === id ? { id, question, source: "pipeline", at, state: "error", error: errorMessage(error) } : run)));
      if (error instanceof ApiError && error.lostConnection) setConnection(null);
    }
  };

  const all = [...runs, ...askRuns];
  const current = all.find((run) => run.id === selected) ?? null;

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-6 sm:px-6 lg:py-8">
      <PageHeader title="Pipeline" description="See every step behind an answer: how the question was rewritten, which tables were found, the SQL that was written and checked, and how long each part took." />

      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
            className="fx-surface rounded-2xl border bg-card p-3 transition-[border-color,box-shadow] duration-200 focus-within:border-primary/40 focus-within:shadow-[0_0_0_4px_color-mix(in_oklch,var(--primary)_10%,transparent)]"
          >
            <label htmlFor="pipeline-question" className="sr-only">
              Question to run
            </label>
            <textarea
              id="pipeline-question"
              rows={3}
              value={text}
              disabled={!connection}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void send();
                }
              }}
              placeholder={connection ? "Ask a question to watch it run…" : "Connect a database to run questions"}
              className="scrollbar-thin block w-full resize-none bg-transparent px-1 text-[14px] leading-relaxed placeholder:text-muted-foreground/70 focus:outline-none disabled:cursor-not-allowed"
            />
            <div className="mt-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Switch id="pipeline-cache" checked={useCache} onCheckedChange={setUseCache} />
                <Label htmlFor="pipeline-cache" className="text-xs font-normal text-muted-foreground">
                  Reuse saved answers
                </Label>
              </div>
              <Button type="submit" size="icon-sm" className="rounded-lg" disabled={!text.trim() || busy || !connection} aria-label="Run question">
                {busy ? <Loader2 className="animate-spin" /> : <ArrowUp />}
              </Button>
            </div>
          </form>
          {ready && !connection && (
            <Button asChild variant="outline" className="w-full">
              <Link href="/connect">
                <Database /> Connect a database
              </Link>
            </Button>
          )}

          <div className="fx-surface rounded-2xl border bg-card p-2">
            <p className="px-2.5 pt-1.5 pb-1 text-[11px] font-medium tracking-wider text-muted-foreground/80 uppercase">Runs on this page</p>
            {runs.length === 0 ? (
              <p className="px-2.5 pb-2 text-xs text-muted-foreground">None yet.</p>
            ) : (
              <div className="space-y-px">
                {runs.map((run) => (
                  <RunButton key={run.id} run={run} active={run.id === selected} onSelect={() => setSelected(run.id)} />
                ))}
              </div>
            )}
            <p className="mt-2 border-t px-2.5 pt-3 pb-1 text-[11px] font-medium tracking-wider text-muted-foreground/80 uppercase">From your Ask chats</p>
            {askRuns.length === 0 ? (
              <p className="px-2.5 pb-2 text-xs text-muted-foreground">Answers you get on Ask in this tab show here.</p>
            ) : (
              <div className="scrollbar-thin max-h-72 space-y-px overflow-y-auto">
                {askRuns.map((run) => (
                  <RunButton key={run.id} run={run} active={run.id === selected} onSelect={() => setSelected(run.id)} />
                ))}
              </div>
            )}
          </div>
        </aside>

        <section className="min-w-0" aria-live="polite">
          {!current ? (
            <Overview />
          ) : current.state === "running" ? (
            <RunningView question={current.question} startedAt={current.at} />
          ) : current.state === "error" ? (
            <div className="fx-enter rounded-2xl border border-critical/30 bg-critical/5 p-6">
              <div className="flex items-start gap-3">
                <XCircle className="mt-0.5 size-5 shrink-0 text-critical" />
                <div>
                  <p className="font-semibold">This run did not finish</p>
                  <p className="mt-1 text-[13px] text-muted-foreground">{current.error}</p>
                  <Button variant="outline" size="sm" className="mt-4" asChild>
                    <Link href={`/ask?q=${encodeURIComponent(current.question)}&mode=ask`}>
                      <MessageSquareText /> Try it in Ask
                    </Link>
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <RunView key={current.id} run={current} />
          )}
        </section>
      </div>
    </div>
  );
}
