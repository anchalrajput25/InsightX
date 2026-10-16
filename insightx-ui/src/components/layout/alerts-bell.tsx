"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Bell, BellRing, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { alertsApi } from "@/lib/api/alerts";
import { stripEmoji, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

export function useOpenAlertCount(): number {
  const summary = useQuery({
    queryKey: ["alerts", "summary"],
    queryFn: () => alertsApi.summary(),
    refetchInterval: 60_000,
    retry: false,
  });
  return summary.data?.open ?? 0;
}

export function AlertsBell() {
  const summary = useQuery({
    queryKey: ["alerts", "summary"],
    queryFn: () => alertsApi.summary(),
    refetchInterval: 60_000,
    retry: false,
  });
  const open = summary.data?.open ?? 0;
  const critical = summary.data?.critical ?? 0;
  const events = useQuery({
    queryKey: ["alerts", "events", "open", "bell"],
    queryFn: () => alertsApi.events("open"),
    enabled: open > 0,
  });

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={open ? `${open} open alerts` : "Alerts"} className="relative text-muted-foreground hover:text-foreground">
          {open ? <BellRing /> : <Bell />}
          {open > 0 && (
            <span
              className={cn(
                "tabular absolute -right-0.5 -top-0.5 min-w-4 rounded-full px-1 text-[10px] font-semibold leading-4 text-white",
                critical ? "bg-critical" : "bg-warning text-black",
              )}
            >
              {open > 9 ? "9+" : open}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <p className="text-sm font-medium">Alerts</p>
          <Link href="/alerts" className="text-xs text-primary hover:underline">
            Open alerts
          </Link>
        </div>
        {open === 0 ? (
          <div className="flex flex-col items-center gap-1.5 px-4 py-8 text-center">
            <CheckCircle2 className="size-5 text-success" />
            <p className="text-sm font-medium">All clear</p>
            <p className="text-xs text-muted-foreground">No metric needs your attention.</p>
          </div>
        ) : events.isPending ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : (
          <ul className="scrollbar-thin max-h-80 divide-y overflow-y-auto">
            {(events.data?.events ?? []).slice(0, 6).map((event) => (
              <li key={event.id}>
                <Link href={`/alerts?event=${event.id}`} className="flex gap-3 px-4 py-3 transition-colors hover:bg-accent">
                  <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", event.severity === "critical" ? "bg-critical" : "bg-warning")} />
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] font-medium">{stripEmoji(event.title || event.alert_name)}</span>
                    <span className="block text-xs text-muted-foreground">
                      {event.alert_name} · {timeAgo(event.ts)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
