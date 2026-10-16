import * as React from "react";
import { cn } from "@/lib/utils";

/** The InsightX mark: a lens over rising bars. */
export function LogoMark({ className }: { className?: string }) {
  const id = React.useId();
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-7", className)}>
      <defs>
        <linearGradient id={`${id}-bg`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--chart-1)" />
          <stop offset="1" stopColor="var(--chart-4)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${id}-bg)`} />
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5" fill="none" stroke="white" strokeOpacity="0.25" />
      <rect x="8" y="17" width="3.2" height="7" rx="1.2" fill="white" fillOpacity="0.6" />
      <rect x="14.4" y="13" width="3.2" height="11" rx="1.2" fill="white" fillOpacity="0.8" />
      <rect x="20.8" y="9" width="3.2" height="15" rx="1.2" fill="white" />
    </svg>
  );
}

export function Logo({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark className="drop-shadow-[0_2px_10px_var(--glow)]" />
      {!collapsed && (
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-tight">InsightX</div>
          <div className="text-[11px] text-muted-foreground">Data investigations</div>
        </div>
      )}
    </div>
  );
}
