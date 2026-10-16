import { cn } from "@/lib/utils";

/** Grid or dot pattern that fades out from the top edge. Decorative only. */
export function GridPattern({ variant = "grid", className }: { variant?: "grid" | "dots"; className?: string }) {
  return <div aria-hidden className={cn("pointer-events-none absolute inset-0 -z-10", variant === "grid" ? "fx-grid" : "fx-dots", className)} />;
}
