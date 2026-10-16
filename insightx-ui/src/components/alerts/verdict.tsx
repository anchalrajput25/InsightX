import { AlertTriangle, CheckCircle2, Clock3, OctagonAlert } from "lucide-react";
import { formatNumber, formatPercent, stripEmoji } from "@/lib/format";
import type { Evaluation } from "@/lib/types";
import { cn } from "@/lib/utils";

export function Verdict({ evaluation }: { evaluation: Evaluation }) {
  const fired = evaluation.fired;
  const critical = evaluation.severity === "critical";
  const Icon = fired ? (critical ? OctagonAlert : AlertTriangle) : evaluation.status === "ok" ? CheckCircle2 : Clock3;
  const tone = fired ? (critical ? "critical" : "warning") : evaluation.status === "ok" ? "success" : "muted";
  const head = fired
    ? `${critical ? "Critical" : "Warning"}: ${stripEmoji(evaluation.title)}`
    : evaluation.status === "ok"
      ? "Normal right now"
      : stripEmoji(evaluation.title) || "Not enough data yet";
  return (
    <div
      className={cn(
        "flex gap-3 rounded-lg border px-3.5 py-3",
        tone === "critical" && "border-critical/30 bg-critical/[0.06]",
        tone === "warning" && "border-warning/30 bg-warning/[0.06]",
        tone === "success" && "border-success/25 bg-success/[0.05]",
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "critical" && "text-critical",
          tone === "warning" && "text-warning",
          tone === "success" && "text-success",
          tone === "muted" && "text-muted-foreground",
        )}
      />
      <div className="min-w-0 space-y-1">
        <p className="text-[13px] font-medium">{head}</p>
        {evaluation.message && <p className="text-[13px] text-muted-foreground">{stripEmoji(evaluation.message)}</p>}
        {evaluation.value !== null && (
          <p className="tabular text-xs text-muted-foreground">
            Latest {formatNumber(evaluation.value)}
            {evaluation.period ? ` (${evaluation.period})` : ""}
            {evaluation.expected !== null ? ` · expected ${formatNumber(evaluation.expected)}` : ""}
            {evaluation.low !== null && evaluation.high !== null ? ` · normal ${formatNumber(evaluation.low)} to ${formatNumber(evaluation.high)}` : ""}
            {evaluation.change_pct !== null ? ` · ${formatPercent(evaluation.change_pct)}` : ""}
          </p>
        )}
      </div>
    </div>
  );
}
