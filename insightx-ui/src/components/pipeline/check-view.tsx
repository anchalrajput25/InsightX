import { CircleCheck, CircleX, TriangleAlert } from "lucide-react";
import { ValueView, isEmpty } from "@/components/pipeline/value-view";
import type { CheckResult } from "@/components/pipeline/pipeline-types";
import { humanize } from "@/lib/format";

export function checkPassed(check: CheckResult | undefined, key: "valid" | "safe" = "valid"): boolean | null {
  if (!check || isEmpty(check)) return null;
  if (typeof check[key] === "boolean") return check[key] as boolean;
  const status = String(check.status ?? "").toLowerCase();
  if (!status) return null;
  return !/(fail|invalid|unsafe|error|block)/.test(status);
}

/** A pass or fail check (SQL safety, SQL validation, result validation). */
export function CheckView({ check, passKey = "valid" }: { check?: CheckResult; passKey?: "valid" | "safe" }) {
  if (!check || isEmpty(check)) return <p className="text-[13px] text-muted-foreground">This check did not report anything for this run.</p>;
  const passed = checkPassed(check, passKey);
  const errors = check.errors ?? [];
  const warnings = check.warnings ?? [];
  return (
    <div className="space-y-3 text-[13px]">
      <div className="flex items-start gap-2">
        {passed === false ? <CircleX className="mt-0.5 size-4 shrink-0 text-critical" /> : <CircleCheck className="mt-0.5 size-4 shrink-0 text-success" />}
        <p>
          <span className="font-medium">{passed === false ? "Did not pass" : "Passed"}</span>
          {check.status ? <span className="text-muted-foreground"> ({humanize(String(check.status))})</span> : null}
          {check.reason ? <span className="text-muted-foreground">: {String(check.reason)}</span> : null}
        </p>
      </div>
      {errors.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-critical/25 bg-critical/5 p-3">
          {errors.map((text, index) => (
            <li key={index} className="flex gap-2 text-critical">
              <CircleX className="mt-0.5 size-3.5 shrink-0" /> <span>{text}</span>
            </li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-warning/25 bg-warning/5 p-3">
          {warnings.map((text, index) => (
            <li key={index} className="flex gap-2 text-warning">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" /> <span>{text}</span>
            </li>
          ))}
        </ul>
      )}
      {!isEmpty(check.details) && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Details</p>
          <ValueView value={check.details} />
        </div>
      )}
    </div>
  );
}
