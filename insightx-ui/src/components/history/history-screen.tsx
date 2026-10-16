"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, ChevronLeft, ChevronRight, Compass, History, MessageSquare, MessageSquareText, RotateCcw, Search, Trash2 } from "lucide-react";
import { ActivityGrid } from "@/components/history/activity-grid";
import { RichText } from "@/components/ask/answer-card";
import { CodeBlock } from "@/components/common/code-block";
import { PageHeader } from "@/components/common/page-header";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { historyApi } from "@/lib/api/history";
import { errorMessage } from "@/lib/api/client";
import { formatDateTime, formatNumber, formatPercent, stripEmoji, timeAgo } from "@/lib/format";
import type { HistoryItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE = 25;

function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

function StatusBadge({ status }: { status: string }) {
  const upper = status.toUpperCase();
  if (upper === "SUCCESS") return <Badge variant="success">Answered</Badge>;
  if (upper === "CLARIFICATION") return <Badge variant="info">Asked back</Badge>;
  if (upper === "ERROR") return <Badge variant="critical">Failed</Badge>;
  return <Badge variant="outline">{status || "Unknown"}</Badge>;
}

function Row({ item, onDelete }: { item: HistoryItem; onDelete: () => void }) {
  const [open, setOpen] = React.useState(false);
  const investigation = item.kind === "investigation";
  const detail = item.detail as { metric?: string; baseline_label?: string; current_label?: string; baseline?: number; current?: number; change_pct?: number | null; reasons?: string[] } | undefined;
  return (
    <li>
      <div className="flex items-start gap-3 px-4 py-3">
        <span className={cn("mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border", investigation ? "bg-primary/10 text-primary" : "bg-surface text-muted-foreground")}>
          {investigation ? <Compass className="size-3.5" /> : <MessageSquareText className="size-3.5" />}
        </span>
        <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} className="min-w-0 flex-1 text-left">
          <span className="flex items-center gap-2">
            <span className="truncate text-[13.5px] font-medium">{stripEmoji(item.question)}</span>
            <ChevronDown className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
          </span>
          {item.answer && !open && <span className="mt-0.5 line-clamp-1 block text-[13px] text-muted-foreground">{stripEmoji(item.answer)}</span>}
          <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <StatusBadge status={item.status} />
            <span title={formatDateTime(item.ts)}>{timeAgo(item.ts)}</span>
            {item.database && <span>{item.database}</span>}
          </span>
        </button>
        <div className="flex shrink-0 items-center gap-0.5">
          {item.thread_id && (
            <Button variant="ghost" size="icon-sm" asChild aria-label="Open chat">
              <Link href={`/ask?chat=${encodeURIComponent(item.thread_id)}`}>
                <MessageSquare className="size-3.5" />
              </Link>
            </Button>
          )}
          <Button variant="ghost" size="icon-sm" asChild aria-label="Ask again">
            <Link href={`/ask?q=${encodeURIComponent(item.question)}${investigation ? "&mode=investigate" : "&mode=ask"}`}>
              <RotateCcw className="size-3.5" />
            </Link>
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label="Delete from history" onClick={onDelete} className="hover:text-critical">
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>
      {open && (
        <div className="space-y-3 border-t bg-surface/40 px-4 py-4 sm:pl-14">
          {item.answer ? <RichText text={item.answer} className="text-sm leading-relaxed" /> : <p className="text-sm text-muted-foreground">No answer was saved.</p>}
          {investigation && detail?.metric && (
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge variant="outline">
                {detail.metric}: {formatNumber(detail.baseline)} ({detail.baseline_label}) to {formatNumber(detail.current)} ({detail.current_label})
              </Badge>
              {typeof detail.change_pct === "number" && <Badge variant={detail.change_pct < 0 ? "critical" : "success"}>{formatPercent(detail.change_pct)}</Badge>}
            </div>
          )}
          {investigation && detail?.reasons?.length ? (
            <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-muted-foreground">
              {detail.reasons.map((reason) => (
                <li key={reason}>{stripEmoji(reason)}</li>
              ))}
            </ul>
          ) : null}
          {item.sql && <CodeBlock code={item.sql} />}
        </div>
      )}
    </li>
  );
}

export function HistoryScreen() {
  const queryClient = useQueryClient();
  const [search, setSearch] = React.useState("");
  const [kind, setKind] = React.useState<"" | "question" | "investigation">("");
  const [page, setPage] = React.useState(0);
  const term = useDebounced(search.trim());
  React.useEffect(() => setPage(0), [term, kind]);

  const history = useQuery({
    queryKey: ["history", term, kind, page],
    queryFn: () => historyApi.list(term, kind, PAGE, page * PAGE),
    placeholderData: keepPreviousData,
  });
  const remove = useMutation({
    mutationFn: (id: string) => historyApi.remove(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["history"] });
      toast.success("Removed from history");
    },
    onError: (error) => toast.error("Could not delete", { description: errorMessage(error) }),
  });
  const clear = useMutation({
    mutationFn: () => historyApi.clear(),
    onSuccess: ({ deleted }) => {
      void queryClient.invalidateQueries({ queryKey: ["history"] });
      toast.success(`Cleared ${deleted} entr${deleted === 1 ? "y" : "ies"}`);
    },
    onError: (error) => toast.error("Could not clear history", { description: errorMessage(error) }),
  });

  const total = history.data?.total ?? 0;
  const items = history.data?.items ?? [];
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-8 sm:px-8">
      <PageHeader
        title="History"
        description="Every question and investigation you ran, across all chats and databases."
        actions={
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={!total || clear.isPending}>
                <Trash2 /> Clear all
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Clear your whole history?</AlertDialogTitle>
                <AlertDialogDescription>This deletes every saved question and answer. Your chats stay in the sidebar. This cannot be undone.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction destructive onClick={() => clear.mutate()}>
                  Clear history
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        }
      />

      {history.data?.activity && history.data.activity.length > 0 && !term && !kind && (
        <section className="rounded-xl border bg-card p-4">
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-sm font-medium">Activity</h2>
            <span className="text-xs text-muted-foreground">{formatNumber(total)} in total</span>
          </div>
          <ActivityGrid activity={history.data.activity} />
        </section>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search questions and answers" className="pl-9" aria-label="Search history" />
        </div>
        <div role="radiogroup" aria-label="Kind" className="flex gap-0.5 rounded-lg bg-muted/60 p-0.5">
          {(
            [
              ["", "All"],
              ["question", "Questions"],
              ["investigation", "Investigations"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={kind === value}
              onClick={() => setKind(value)}
              className={cn("h-8 rounded-md px-3 text-xs font-medium text-muted-foreground hover:text-foreground", kind === value && "bg-card text-foreground shadow-sm")}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border bg-card">
        {history.isPending ? (
          <div className="p-4">
            <RowsSkeleton rows={6} />
          </div>
        ) : history.isError ? (
          <ErrorState error={history.error} onRetry={() => void history.refetch()} className="m-4" />
        ) : items.length === 0 ? (
          <EmptyState
            compact
            icon={History}
            title={term || kind ? "No matches" : "No history yet"}
            description={term || kind ? "Try other words or another filter." : "Questions you ask on the Ask page appear here."}
            className="py-14"
          />
        ) : (
          <ul className={cn("divide-y transition-opacity", history.isFetching && "opacity-70")}>
            {items.map((item) => (
              <Row key={item.id} item={item} onDelete={() => remove.mutate(item.id)} />
            ))}
          </ul>
        )}
        {total > PAGE && (
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
            <span className="tabular">
              {page * PAGE + 1} to {Math.min(total, (page + 1) * PAGE)} of {formatNumber(total)}
            </span>
            <div className="flex gap-1">
              <Button variant="ghost" size="icon-sm" aria-label="Previous page" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>
                <ChevronLeft />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label="Next page" disabled={page + 1 >= pages} onClick={() => setPage((value) => value + 1)}>
                <ChevronRight />
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
