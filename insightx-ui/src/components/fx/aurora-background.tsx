import { cn } from "@/lib/utils";

/**
 * Aurora background (21st.dev style): slow, blurred colour fields behind content.
 * Purely decorative and hidden from screen readers.
 */
export function AuroraBackground({ className, intensity = 1 }: { className?: string; intensity?: number }) {
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 -z-10 overflow-hidden [mask-image:radial-gradient(ellipse_at_center,black_35%,transparent_78%)]", className)} style={{ opacity: intensity }}>
      <div className="fx-aurora-blob absolute -left-[10%] -top-[20%] size-[55%] rounded-full bg-[var(--glow)] blur-[90px] [animation:fx-float_18s_ease-in-out_infinite_alternate]" />
      <div className="fx-aurora-blob absolute -right-[5%] top-[5%] size-[45%] rounded-full bg-[var(--glow-2)] blur-[100px] [animation:fx-float_22s_ease-in-out_infinite_alternate-reverse]" />
      <div className="fx-aurora-blob absolute bottom-[-25%] left-[25%] size-[50%] rounded-full bg-chart-4/15 blur-[110px] [animation:fx-float_26s_ease-in-out_infinite_alternate]" />
    </div>
  );
}
