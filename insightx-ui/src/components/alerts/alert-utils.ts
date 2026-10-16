import { formatNumber } from "@/lib/format";
import type { AlertEvent, AlertRule, RuleType } from "@/lib/types";

export const RULE_TYPES: { value: RuleType; label: string; help: string }[] = [
  { value: "smart", label: "Anything unusual", help: "Learns the normal level from the past (trend, weekday habits, usual ups and downs) and warns when the latest value is far outside it. No limits to set." },
  { value: "limit", label: "Crosses a limit", help: "Warns when the number goes above or below a fixed value you choose." },
  { value: "change", label: "Sudden jump or drop", help: "Warns when the number moves a lot compared with the period before, the same day last week, or the same time last year." },
  { value: "trend", label: "Keeps falling or rising", help: "Warns when the number keeps going the same way several periods in a row." },
  { value: "stale", label: "Data stops arriving", help: "Warns when no new rows arrive for a while: a broken data feed or a stopped process." },
];

export const DIRECTIONS = { both: "Rises and drops", up: "Only rises", down: "Only drops" } as const;
export const COMPARE = { previous: "the period before", week: "the same day last week", year: "the same time last year" } as const;
export const SEVERITY = { auto: "Auto (by how far off it is)", warning: "Always warning", critical: "Always critical" } as const;
export const FREQUENCY: Record<number, string> = {
  5: "Every 5 minutes",
  15: "Every 15 minutes",
  30: "Every 30 minutes",
  60: "Every hour",
  180: "Every 3 hours",
  360: "Every 6 hours",
  720: "Every 12 hours",
  1440: "Once a day",
};

export const STATE: Record<string, { label: string; tone: "success" | "critical" | "warning" | "outline" | "info" }> = {
  ok: { label: "Normal", tone: "success" },
  fired: { label: "Alert", tone: "critical" },
  new: { label: "Waiting for first check", tone: "outline" },
  error: { label: "Problem", tone: "warning" },
  not_enough_data: { label: "Learning", tone: "info" },
  no_data: { label: "No data", tone: "outline" },
};

export const DEFAULT_RULE: AlertRule = {
  type: "smart",
  direction: "both",
  sensitivity: "medium",
  op: "above",
  value: 0,
  percent: 30,
  compare_to: "previous",
  periods: 3,
  severity: "auto",
  skip_unfinished: true,
};

function unitOf(grain: string | null | undefined): string {
  return grain && ["day", "week", "month", "quarter", "year"].includes(grain) ? grain : "check";
}

/** The rule in one plain sentence (same wording as the Streamlit page). */
export function describeRule(rule: AlertRule, grain?: string | null): string {
  const unit = unitOf(grain);
  const direction = rule.direction ?? "both";
  switch (rule.type) {
    case "smart": {
      const way = { both: "", up: ", only rises", down: ", only drops" }[direction];
      return `Warn when it looks unusual (${rule.sensitivity ?? "medium"} sensitivity${way})`;
    }
    case "limit":
      return `Warn when it goes ${rule.op ?? "above"} ${formatNumber(rule.value ?? 0)}`;
    case "change": {
      const verb = { both: "jumps or drops", up: "jumps", down: "drops" }[direction];
      return `Warn when it ${verb} ${formatNumber(rule.percent ?? 30)}% vs ${COMPARE[rule.compare_to ?? "previous"]}`;
    }
    case "trend": {
      const verb = { both: "falls or rises", up: "rises", down: "falls" }[direction];
      return `Warn when it ${verb} ${rule.periods ?? 3} ${unit}s in a row`;
    }
    default:
      return `Warn when there is no new data for ${rule.periods ?? 2} ${unit}s`;
  }
}

export function unitLabel(grain: string | null | undefined): string {
  return unitOf(grain);
}

/** The question "Find out why" asks on the Ask page. */
export function whyQuestion(event: Pick<AlertEvent, "value" | "expected" | "change_pct" | "alert_name" | "period">): string {
  const { value, expected, change_pct: pct } = event;
  const wentDown = (value !== null && expected !== null && value < expected) || (pct !== null && pct < 0);
  let name = String(event.alert_name || "this number");
  name = name.charAt(0).toLowerCase() + name.slice(1);
  const period = String(event.period || "").trim();
  let when = "";
  if (period && period !== "latest reading" && !period.startsWith("the check")) {
    when = /^\d/.test(period) ? ` on ${period}` : ` in ${period}`;
  }
  return `Why did ${name} ${wentDown ? "drop" : "go up"}${when}?`;
}
