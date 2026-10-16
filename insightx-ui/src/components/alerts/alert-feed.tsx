"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, CheckCheck, CheckCircle2, Clock, Eye, OctagonAlert, RotateCcw, SearchCheck } from "lucide-react";
import { whyQuestion } from "@/components/alerts/alert-utils";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { alertsApi } from "@/lib/api/alerts";
import { errorMessage } from "@/lib/api/client";
import { formatDateTime, formatNumber, formatPercent, stripEmoji, timeAgo } from "@/lib/format";
import type { AlertEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

type Filter = "open" | "all" | "resolved";

function EventRow({ event, highlighted, onAction, busy }: { event: AlertEvent; highlighted: boolean; onAction: (action: "acknowledge" | "resolve" | "reopen") => void; busy: boolean }) {
  const router = useRouter();
  const ref = React.useRef<HTMLLIElement>(null);
  const critical = event.severity === "critical";
  React.useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlighted]);
  const Icon = event.status === "resolved" ? CheckCircle2 : critical ? OctagonAlert : AlertTriangle;
  return (
    <li
      ref={ref}
      className={cn(
        "group relative flex flex-col gap-3 px-4 py-4 transition-colors sm:flex-row sm:items-start",
        highlighted && "bg-primary/[0.06]",
        event.status === "resolved" && "opacity-70",
      )}
    >
      <span
        aria-hidden
        className={cn("absolute inset-y-3 left-0 w-0.5 rounded-full", event.status === "resolved" ? "bg-border" : critical ? "bg-critical" : "bg-warning")}
      />
      <Icon className={cn("mt-0.5 size-4 shrink-0", event.status === "resolved" ? "text-success" : critical ? "text-critical" : "text-warning")} />
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13.5px] font-medium">{stripEmoji(event.title || event.alert_name)}</p>
          <Badge variant={critical ? "critical" : "warning"} className="capitalize">
            {event.severity}
          </Badge>
          {event.status === "acknowledged" && (
            <Badge variant="outline">
              <Eye /> Seen
            </Badge>
          )}
          {event.status === "resolved" && <Badge variant="success">Resolved</Badge>}
        </div>
        {event.message && <p className="text-[13px] leading-relaxed text-muted-foreground">{stripEmoji(event.message)}</p>}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">{event.alert_name}</span>
          {event.period && <span>{event.period}</span>}
          {event.value !== null && (
            <span className="tabular">
              {formatNumber(event.value)}
              {event.expected !== null && <> vs {formatNumber(event.expected)} expected</>}
            </span>
          )}
          {event.change_pct !== null && <span className={cn("tabular", event.change_pct < 0 ? "text-critical" : "text-success")}>{formatPercent(event.change_pct)}</span>}
          <span className="inline-flex items-center gap-1" title={formatDateTime(event.ts)}>
            <Clock className="size-3" /> {timeAgo(event.ts)}
          </span>
          {event.notified && <span>Sent: {event.notified}</span>}
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap gap-1.5 pl-7 sm:pl-0">
        <Button
          variant="outline"
          size="xs"
          onClick={() => router.push(`/ask?q=${encodeURIComponent(whyQuestion(event))}&mode=investigate`)}
        >
          <SearchCheck /> Find out why
        </Button>
        {event.status === "open" && (
          <Button variant="ghost" size="xs" disabled={busy} onClick={() => onAction("acknowledge")}>
            <Eye /> Seen
          </Button>
        )}
        {event.status !== "resolved" ? (
          <Button variant="ghost" size="xs" disabled={busy} onClick={() => onAction("resolve")}>
            <CheckCheck /> Resolve
          </Button>
        ) : (
          <Button variant="ghost" size="xs" disabled={busy} onClick={() => onAction("reopen")}>
            <RotateCcw /> Reopen
          </Button>
        )}
      </div>
    </li>
  );
}

export function AlertFeed({ connectionId, onCreate }: { connectionId: string; onCreate: () => void }) {
  const params = useSearchParams();
  const highlight = params.get("event");
  const queryClient = useQueryClient();
  const [filter, setFilter] = React.useState<Filter>("open");
  const events = useQuery({
    queryKey: ["alerts", "events", filter, connectionId],
    queryFn: () => alertsApi.events(filter, connectionId || undefined),
    refetchInterval: 60_000,
  });
  const action = useMutation({
    mutationFn: ({ id, act }: { id: string; act: "acknowledge" | "resolve" | "reopen" }) => alertsApi.eventAction(id, act),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["alerts"] }),
    onError: (error) => toast.error("Could not update the alert", { description: errorMessage(error) }),
  });

  const list = events.data?.events ?? [];
  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-2.5">
        <div role="radiogroup" aria-label="Show" className="flex gap-0.5 rounded-lg bg-muted/60 p-0.5">
          {(["open", "all", "resolved"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={filter === value}
              onClick={() => setFilter(value)}
              className={cn("h-7 rounded-md px-3 text-xs font-medium capitalize text-muted-foreground hover:text-foreground", filter === value && "bg-card text-foreground shadow-sm")}
            >
              {value}
            </button>
          ))}
        </div>
        {list.length > 0 && <span className="text-xs text-muted-foreground">{list.length} shown</span>}
      </div>
      {events.isPending ? (
        <div className="p-4">
          <RowsSkeleton rows={4} />
        </div>
      ) : events.isError ? (
        <ErrorState error={events.error} onRetry={() => void events.refetch()} className="m-4" />
      ) : list.length === 0 ? (
        <EmptyState
          compact
          icon={CheckCircle2}
          title={filter === "open" ? "All clear" : "Nothing here yet"}
          description={filter === "open" ? "No metric is outside its normal range right now." : "Raised alerts are listed here."}
          action={
            <Button variant="outline" size="sm" onClick={onCreate}>
              New alert <ArrowRight />
            </Button>
          }
          className="py-14"
        />
      ) : (
        <ul className="divide-y">
          {list.map((event) => (
            <EventRow
              key={event.id}
              event={event}
              highlighted={event.id === highlight}
              busy={action.isPending}
              onAction={(act) => action.mutate({ id: event.id, act })}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
