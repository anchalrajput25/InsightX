"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Spotlight card (21st.dev style): a soft glow and a lit border follow the pointer.
 * Renders a plain div, so it can wrap any card content.
 */
export const SpotlightCard = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, onPointerMove, ...props }, ref) => (
    <div
      ref={ref}
      onPointerMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        event.currentTarget.style.setProperty("--mx", `${event.clientX - rect.left}px`);
        event.currentTarget.style.setProperty("--my", `${event.clientY - rect.top}px`);
        onPointerMove?.(event);
      }}
      className={cn("fx-spotlight fx-surface", className)}
      {...props}
    />
  ),
);
SpotlightCard.displayName = "SpotlightCard";
