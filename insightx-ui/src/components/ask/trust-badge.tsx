"use client";

import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { Trust } from "@/lib/types";
import { cn } from "@/lib/utils";

const LEVELS = {
  high: { label: "High trust", variant: "success", icon: ShieldCheck },
  medium: { label: "Medium trust", variant: "warning", icon: ShieldQuestion },
  low: { label: "Low trust", variant: "critical", icon: ShieldAlert },
} as const;

export function trustLevel(trust?: Trust) {
  const key = (trust?.level ?? "").toLowerCase() as keyof typeof LEVELS;
  return LEVELS[key] ?? null;
}

export function TrustReasons({ trust }: { trust: Trust }) {
  const reasons = trust.reasons ?? [];
  if (reasons.length === 0) return <p className="text-sm text-muted-foreground">No checks were reported for this answer.</p>;
  return (
    <ul className="space-y-2">
      {reasons.map((reason, index) => (
        <li key={`${reason.signal}-${index}`} className="flex gap-3 text-[13px]">
          <span
            className={cn(
              "tabular mt-0.5 w-10 shrink-0 rounded px-1 text-center text-[11px] font-semibold leading-5",
              reason.effect < 0 ? "bg-warning/10 text-warning" : "bg-success/10 text-success",
            )}
          >
            {reason.effect < 0 ? reason.effect : "ok"}
          </span>
          <span>
            <span className="font-medium capitalize">{reason.signal.replace(/_/g, " ")}</span>
            <span className="text-muted-foreground">: {reason.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function TrustBadge({ trust }: { trust?: Trust }) {
  const level = trustLevel(trust);
  if (!trust || !level) return null;
  const Icon = level.icon;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="rounded-md focus-visible:ring-2 focus-visible:ring-ring" aria-label={`${level.label}, score ${trust.score ?? "unknown"}. Show checks`}>
          <Badge variant={level.variant} className="cursor-pointer">
            <Icon /> {level.label}
            {typeof trust.score === "number" && <span className="tabular opacity-80">{trust.score}</span>}
          </Badge>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)]">
        <p className="mb-3 text-sm font-medium">Why this trust score</p>
        <TrustReasons trust={trust} />
      </PopoverContent>
    </Popover>
  );
}
