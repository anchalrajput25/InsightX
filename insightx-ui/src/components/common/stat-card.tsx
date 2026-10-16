import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { NumberTicker } from "@/components/fx/number-ticker";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { cn } from "@/lib/utils";

const TONES = {
  default: "text-muted-foreground bg-muted",
  primary: "text-primary bg-primary/12",
  success: "text-success bg-success/12",
  warning: "text-warning bg-warning/12",
  critical: "text-critical bg-critical/12",
} as const;

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
  className,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  icon?: LucideIcon;
  tone?: keyof typeof TONES;
  className?: string;
}) {
  return (
    <SpotlightCard className={cn("rounded-xl border bg-card p-4 transition-colors duration-200 hover:border-foreground/12", className)}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {Icon && (
          <span className={cn("flex size-7 items-center justify-center rounded-md", TONES[tone])}>
            <Icon className="size-3.5" />
          </span>
        )}
      </div>
      <p className="tabular mt-2 text-[26px] leading-tight font-semibold tracking-tight">
        <NumberTicker value={value} />
      </p>
      {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
    </SpotlightCard>
  );
}
