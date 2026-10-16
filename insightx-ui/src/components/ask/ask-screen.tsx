"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, Compass, Database, History, Loader2, MessageSquareText, RefreshCw, RotateCcw, Unplug } from "lucide-react";
import { LogoMark } from "@/components/common/logo";
import { AuroraBackground } from "@/components/fx/aurora-background";
import { EmptyState } from "@/components/common/states";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AnswerCard, RichText } from "@/components/ask/answer-card";
import { Composer } from "@/components/ask/composer";
import { InvestigationView } from "@/components/ask/investigation-view";
import { loadTurns, saveTurns, turnsFromHistory, type Mode, type Turn } from "@/components/ask/turns";
import { useAuth } from "@/components/providers/auth-provider";
import { useConnection } from "@/components/providers/connection-provider";
import { useChatMutations } from "@/hooks/use-chats";
import { ApiError, errorMessage } from "@/lib/api/client";
import { questionsApi } from "@/lib/api/questions";
import { initials, stripEmoji } from "@/lib/format";
import { newThreadId, readPref, writePref } from "@/lib/ui-prefs";

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="tabular">{Math.max(0, Math.floor((now - since) / 1000))}s</span>;
}

function Question({ text }: { text: string }) {
  const { displayName } = useAuth();
  return (
    <div className="flex justify-end gap-3">
      <div className="fx-enter max-w-[85%] rounded-2xl rounded-tr-md border border-primary/20 bg-[linear-gradient(135deg,color-mix(in_oklch,var(--primary)_18%,var(--secondary)),var(--secondary))] px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap shadow-[inset_0_1px_0_var(--edge)]">{stripEmoji(text)}</div>
      <Avatar className="mt-0.5 size-7 shrink-0">
        <AvatarFallback className="text-[10px] font-semibold">{initials(displayName)}</AvatarFallback>
      </Avatar>
    </div>
  );
}

function AnswerShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <LogoMark className="mt-0.5 size-7 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function TurnView({
  turn,
  busy,
  onAsk,
  onInvestigate,
  onRetry,
}: {
  turn: Turn;
  busy: boolean;
  onAsk: (question: string) => void;
  onInvestigate: (question: string) => void;
  onRetry: (turn: Turn) => void;
}) {
  return (
    <div className="space-y-4">
      <Question text={turn.question} />
      <AnswerShell>
        {turn.state === "pending" && (
          <div className="space-y-3" role="status" aria-live="polite">
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin text-primary" />
              {turn.kind === "investigation" ? "Investigating: comparing periods and testing causes" : "Writing and checking the SQL"}
              <span className="text-muted-foreground/70">
                · <Elapsed since={turn.startedAt} />
              </span>
            </p>
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-3/5" />
            <Skeleton className="h-40 w-full rounded-xl" />
          </div>
        )}
        {turn.state === "error" && (
          <div className="rounded-xl border border-critical/25 bg-critical/[0.05] p-4">
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-critical" />
              <div className="min-w-0 space-y-3">
                <div>
                  <p className="text-sm font-medium">{turn.kind === "investigation" ? "The investigation did not finish" : "No answer this time"}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{turn.error}</p>
                </div>
                <div className="flex gap-2">
                  {turn.lostConnection ? (
                    <Button size="sm" asChild>
                      <Link href="/connect">
                        <Database /> Connect again
                      </Link>
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => onRetry(turn)}>
                      <RefreshCw /> Try again
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
        {turn.state === "done" && turn.kind === "answer" && <AnswerCard result={turn.result} onAsk={onAsk} onInvestigate={onInvestigate} busy={busy} />}
        {turn.state === "done" && turn.kind === "investigation" && <InvestigationView report={turn.result} onAsk={onAsk} busy={busy} />}
        {turn.state === "text" && (
          <div className="space-y-2">
            {turn.answer ? (
              <RichText text={turn.answer} className="text-[15px] leading-relaxed" />
            ) : (
              <p className="text-sm text-muted-foreground">No answer was saved for this question.</p>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => onAsk(turn.question)}
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <RotateCcw className="size-3" /> Ask again to see the table and chart
            </button>
          </div>
        )}
      </AnswerShell>
    </div>
  );
}

function Welcome({ tables, onAsk }: { tables: string[]; onAsk: (question: string, kind?: "answer" | "investigation") => void }) {
  const first = tables[0];
  const second = tables[1] ?? tables[0];
  const ideas: { text: string; icon: typeof MessageSquareText; kind?: "investigation" }[] = first
    ? [
        { text: `How many rows does ${first} have per month?`, icon: MessageSquareText },
        { text: `Show the top 10 ${second} by count`, icon: MessageSquareText },
        { text: "Which tables have the most data?", icon: Database },
        { text: `Why did ${first} change last month?`, icon: Compass, kind: "investigation" },
      ]
    : [];
  return (
    <div className="relative isolate mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-4 py-10 text-center">
      <AuroraBackground intensity={0.75} />
      <div className="fx-enter relative">
        <div aria-hidden className="absolute inset-0 -z-10 scale-[2.6] rounded-full bg-[var(--glow)] blur-2xl" />
        <LogoMark className="size-12" />
      </div>
      <h1 className="fx-enter mt-6 text-3xl font-semibold tracking-tight sm:text-4xl" style={{ ["--d" as string]: "80ms" }}>
        <span className="fx-title">What do you </span>
        <span className="fx-gradient-text">want to know?</span>
      </h1>
      <p className="fx-enter mt-2.5 max-w-md text-sm text-muted-foreground" style={{ ["--d" as string]: "140ms" }}>Ask in plain English. Use Investigate to find out why a number changed.</p>
      {ideas.length > 0 && (
        <div className="fx-stagger mt-8 grid w-full gap-2.5 sm:grid-cols-2">
          {ideas.map((idea) => (
            <button
              key={idea.text}
              type="button"
              onClick={() => onAsk(idea.text, idea.kind)}
              className="fx-spotlight fx-surface group flex cursor-pointer items-start gap-3 rounded-xl border bg-card/60 p-3.5 text-left text-[13px] backdrop-blur-sm transition-colors duration-200 hover:border-primary/40 hover:bg-card"
              onPointerMove={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                event.currentTarget.style.setProperty("--mx", `${event.clientX - rect.left}px`);
                event.currentTarget.style.setProperty("--my", `${event.clientY - rect.top}px`);
              }}
            >
              <idea.icon className="mt-0.5 size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
              <span className="flex-1">{idea.text}</span>
              <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function AskScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const chatParam = params.get("chat");
  const queued = params.get("q");
  const queuedMode = params.get("mode");
  const { connection, ready, setConnection } = useConnection();
  const { touch } = useChatMutations();
  const queryClient = useQueryClient();

  const [threadId, setThreadId] = React.useState(chatParam ?? "");
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [loadingChat, setLoadingChat] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string>("");
  const [mode, setModeState] = React.useState<Mode>("auto");
  const createdHere = React.useRef<string | null>(null);
  const handledQueued = React.useRef<string | null>(null);
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const turnsRef = React.useRef<Turn[]>([]);

  React.useEffect(() => setModeState(readPref<Mode>("askMode", "auto")), []);
  const setMode = (next: Mode) => {
    setModeState(next);
    writePref("askMode", next);
  };

  const updateTurns = React.useCallback((thread: string, change: (turns: Turn[]) => Turn[]) => {
    const next = change(turnsRef.current);
    turnsRef.current = next;
    setTurns(next);
    if (thread) saveTurns(thread, next);
  }, []);

  // Open the chat named in the URL.
  React.useEffect(() => {
    if (!ready) return;
    setLoadError("");
    if (!chatParam) {
      setThreadId("");
      turnsRef.current = [];
      setTurns([]);
      return;
    }
    if (chatParam === createdHere.current) return;
    setThreadId(chatParam);
    const cached = loadTurns(chatParam);
    if (cached) {
      turnsRef.current = cached;
      setTurns(cached);
      return;
    }
    turnsRef.current = [];
    setTurns([]);
    if (!connection) return;
    let cancelled = false;
    setLoadingChat(true);
    questionsApi
      .threadHistory(chatParam, connection.id)
      .then((response) => {
        if (cancelled) return;
        const restored = turnsFromHistory(response.conversation_history ?? []);
        turnsRef.current = restored;
        setTurns(restored);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.lostConnection) setLoadError("lost");
        else setLoadError(errorMessage(error));
      })
      .finally(() => !cancelled && setLoadingChat(false));
    return () => {
      cancelled = true;
    };
  }, [chatParam, ready, connection]);

  const busy = turns.some((turn) => turn.state === "pending");

  // New question: show it at the bottom. New answer: show it from its start.
  const lastTurnRef = React.useRef<HTMLDivElement>(null);
  const lastState = turns[turns.length - 1]?.state;
  React.useEffect(() => {
    if (!turns.length) return;
    if (lastState === "pending") bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    else if (lastState === "done" || lastState === "error") lastTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length, lastState]);

  const send = React.useCallback(
    async (question: string, forced?: "answer" | "investigation") => {
      if (!connection) {
        toast.error("Connect a database first");
        return;
      }
      let thread = threadId;
      if (!thread) {
        thread = newThreadId();
        createdHere.current = thread;
        setThreadId(thread);
        router.replace(`/ask?chat=${encodeURIComponent(thread)}`, { scroll: false });
      }

      const id = newThreadId();
      let kind: "answer" | "investigation" = forced ?? (mode === "investigate" ? "investigation" : "answer");
      updateTurns(thread, (current) => [...current, { id, question, state: "pending", kind, startedAt: Date.now() }]);

      if (!forced && mode === "auto") {
        try {
          kind = (await questionsApi.looksLikeWhy(question)).investigation ? "investigation" : "answer";
        } catch {
          kind = "answer";
        }
        updateTurns(thread, (current) => current.map((turn) => (turn.id === id && turn.state === "pending" ? { ...turn, kind } : turn)));
      }

      try {
        if (kind === "investigation") {
          const result = await questionsApi.investigate(connection.id, question, thread);
          updateTurns(thread, (current) => current.map((turn) => (turn.id === id ? { id, question, state: "done", kind: "investigation", result } : turn)));
        } else {
          const result = await questionsApi.ask(connection.id, question, thread);
          updateTurns(thread, (current) => current.map((turn) => (turn.id === id ? { id, question, state: "done", kind: "answer", result } : turn)));
        }
        touch(thread, question).catch(() => undefined);
        void queryClient.invalidateQueries({ queryKey: ["history"] });
        void queryClient.invalidateQueries({ queryKey: ["traces"] });
      } catch (error) {
        const lost = error instanceof ApiError && error.lostConnection;
        updateTurns(thread, (current) =>
          current.map((turn) => (turn.id === id ? { id, question, state: "error", kind, error: errorMessage(error), lostConnection: lost } : turn)),
        );
        if (lost) setConnection(null);
      }
    },
    [connection, threadId, mode, router, updateTurns, touch, queryClient, setConnection],
  );

  // A question sent from the search box (?q=...).
  React.useEffect(() => {
    if (!ready || !connection || !queued || handledQueued.current === queued) return;
    handledQueued.current = queued;
    if (chatParam) router.replace(`/ask?chat=${encodeURIComponent(chatParam)}`, { scroll: false });
    void send(queued, queuedMode === "investigate" ? "investigation" : queuedMode === "ask" ? "answer" : undefined);
  }, [ready, connection, queued, queuedMode, chatParam, router, send]);

  if (!ready) return null;

  if (!connection) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon={Database}
          title="Connect a database to start"
          description="InsightX answers questions about the database you connect. It only reads your data."
          action={
            <Button asChild>
              <Link href="/connect">
                <Database /> Connect a database
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  const tableNames = (connection.tables ?? []).filter((table) => !table.name.startsWith("insightx_")).map((table) => table.name);
  const retry = (turn: Turn) => {
    updateTurns(threadId, (current) => current.filter((item) => item.id !== turn.id));
    void send(turn.question, turn.state === "error" ? turn.kind : undefined);
  };

  return (
    <div className="flex flex-1 flex-col">
      {loadError === "lost" ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={Unplug}
            title="Your database needs reconnecting"
            description="The backend restarted and no longer has this connection. Connect again to open this chat."
            action={
              <Button
                asChild
                onClick={() => setConnection(null)}
              >
                <Link href="/connect">Connect again</Link>
              </Button>
            }
          />
        </div>
      ) : loadingChat ? (
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8" aria-busy>
          <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="ml-auto h-10 w-1/2 rounded-2xl" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : turns.length === 0 ? (
        loadError ? (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState icon={History} title="This chat could not be opened" description={loadError} action={<Button variant="outline" onClick={() => router.push("/ask")}>Start a new chat</Button>} />
          </div>
        ) : (
          <Welcome tables={tableNames} onAsk={(question, kind) => void send(question, kind)} />
        )
      ) : (
        <div className="mx-auto w-full max-w-3xl flex-1 space-y-10 px-4 pb-8 pt-8 sm:px-6">
          {turns.some((turn) => turn.state === "text") && (
            <p className="rounded-lg border bg-surface px-3 py-2 text-xs text-muted-foreground">
              This chat was opened from your saved history, so earlier answers show their text only.
            </p>
          )}
          {turns.map((turn, index) => (
            <div key={turn.id} ref={index === turns.length - 1 ? lastTurnRef : undefined} className="scroll-mt-20">
              <TurnView
                turn={turn}
                busy={busy}
                onAsk={(question) => void send(question, "answer")}
                onInvestigate={(question) => void send(question, "investigation")}
                onRetry={retry}
              />
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}

      <div className="sticky bottom-0 z-10 bg-gradient-to-t from-background via-background to-background/0 px-4 pb-4 pt-6 sm:px-6">
        <div className="mx-auto w-full max-w-3xl">
          <Composer onSend={(question) => void send(question)} busy={busy} mode={mode} onModeChange={setMode} autoFocus />
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            Answers come from {connection.label}. Check the SQL and trust score before acting on them.
          </p>
        </div>
      </div>
    </div>
  );
}
