// Chart title and short insights worked out from the numbers themselves
// (no AI call, so they are instant, free and always match the data).

import { humanize } from "@/lib/format";
import { formatOf, formatValue } from "@/lib/chart-intel/format";
import type { Prepared } from "@/lib/chart-intel/prepare";
import type { SmartPlan } from "@/lib/chart-intel/recommend";

const GRAIN_WORD: Record<string, string> = { year: "year", quarter: "quarter", month: "month", day: "day", time: "time" };

export function chartTitle(plan: SmartPlan): string {
  const y = humanize(plan.ys[0] ?? "");
  const x = humanize(plan.x);
  const time = plan.profile.columns.find((c) => c.name === plan.x);
  switch (plan.kind) {
    case "metric":
      return plan.ys.length === 1 ? y : "Key figures";
    case "histogram":
      return `How ${humanize(plan.xRole === "numeric" ? plan.x : (plan.ys[0] ?? "")).toLowerCase()} is spread`;
    case "scatter":
    case "bubble":
      return plan.xRole === "numeric"
        ? `${y} vs ${x.toLowerCase()}`
        : `${humanize(plan.ys[1] ?? "")} vs ${y.toLowerCase()} by ${x.toLowerCase()}`;
    case "donut":
      return `Share of ${y.toLowerCase()} by ${x.toLowerCase()}`;
    case "combo":
      return `${y} and ${humanize(plan.ys[1] ?? "").toLowerCase()} by ${x.toLowerCase()}`;
    default: {
      const by = plan.xRole === "temporal" && time?.grain && time.grain !== "time" ? GRAIN_WORD[time.grain] : x.toLowerCase();
      const ys = plan.ys.length > 1 && !plan.split ? plan.ys.map((name, i) => (i ? humanize(name).toLowerCase() : humanize(name))).join(", ") : y;
      const base = plan.xRole === "temporal" ? `${ys} by ${by}` : `${ys} by ${x.toLowerCase()}`;
      return plan.split ? `${base} and ${humanize(plan.split).toLowerCase()}` : base;
    }
  }
}

function pct(change: number): string {
  const value = Math.abs(change) >= 100 ? Math.round(change) : Number(change.toFixed(1));
  return `${value}%`;
}

export function chartInsights(plan: SmartPlan, data: Prepared): string[] {
  const out: string[] = [];
  if (data.type === "category") {
    const main = data.series[0];
    if (!main) return out;
    const fmt = formatOf(plan.profile.columns.find((c) => c.name === main.measure));
    const cats = data.categories;
    const values = main.values;
    if (plan.xRole === "temporal" && !plan.split) {
      const pairs = cats.map((c, i) => [c, values[i]] as const).filter((p): p is readonly [string, number] => typeof p[1] === "number");
      const first = pairs[0];
      const last = pairs[pairs.length - 1];
      if (first && last && pairs.length >= 2 && first[1] !== 0) {
        const change = ((last[1] - first[1]) / Math.abs(first[1])) * 100;
        const word = change > 1 ? "rose" : change < -1 ? "fell" : "stayed about the same";
        out.push(
          word === "stayed about the same"
            ? `${humanize(main.measure)} stayed about the same from ${first[0]} to ${last[0]}.`
            : `${humanize(main.measure)} ${word} ${pct(Math.abs(change))} from ${first[0]} (${formatValue(first[1], fmt)}) to ${last[0]} (${formatValue(last[1], fmt)}).`,
        );
      }
      if (pairs.length >= 3) {
        const peak = pairs.reduce((best, p) => (p[1] > best[1] ? p : best));
        out.push(`Highest point: ${peak[0]} at ${formatValue(peak[1], fmt)}.`);
      }
    } else if (!plan.split) {
      const pairs = cats.map((c, i) => [c, values[i] ?? 0] as const).filter((p) => p[0] !== "Other");
      if (pairs.length >= 2) {
        const ranked = [...pairs].sort((a, b) => b[1] - a[1]);
        const total = (values as (number | null)[]).reduce<number>((sum, v) => sum + (v ?? 0), 0);
        const top = ranked[0] as readonly [string, number];
        const second = ranked[1] as readonly [string, number];
        const additive = plan.aggregation !== "avg" && fmt.unit !== "percent" && !plan.profile.columns.find((c) => c.name === main.measure)?.hasNegative;
        if (additive && total > 0) {
          out.push(`${top[0]} leads with ${formatValue(top[1], fmt)}, ${pct((top[1] / total) * 100)} of the total.`);
          if (ranked.length >= 5) {
            const top3 = ranked.slice(0, 3).reduce((s, p) => s + p[1], 0);
            out.push(`The top 3 make up ${pct((top3 / total) * 100)} of the total.`);
          }
        } else {
          out.push(`${top[0]} is highest at ${formatValue(top[1], fmt)}.`);
        }
        if (second[1] > 0 && top[1] / second[1] >= 1.5) out.push(`${top[0]} is ${(top[1] / second[1]).toFixed(1)}x the next one (${second[0]}).`);
        const lowest = ranked[ranked.length - 1] as readonly [string, number];
        if (ranked.length >= 3) out.push(`Lowest: ${lowest[0]} at ${formatValue(lowest[1], fmt)}.`);
      }
    } else {
      const totals = data.series.map((s) => [s.name, s.values.reduce<number>((a, v) => a + (v ?? 0), 0)] as const).sort((a, b) => b[1] - a[1]);
      const all = totals.reduce((a, t) => a + t[1], 0);
      const top = totals[0];
      if (top && all > 0) out.push(`${top[0]} is the biggest ${humanize(plan.split).toLowerCase()} with ${pct((top[1] / all) * 100)} of the total.`);
    }
  } else if (data.type === "points") {
    const r = data.correlation;
    if (r !== null) {
      const strength = Math.abs(r) >= 0.7 ? "strong" : Math.abs(r) >= 0.4 ? "moderate" : Math.abs(r) >= 0.2 ? "weak" : "no clear";
      const direction = strength === "no clear" ? "" : r > 0 ? "positive " : "negative ";
      out.push(
        `There is a ${strength} ${direction}link between ${humanize(data.xName).toLowerCase()} and ${humanize(data.yName).toLowerCase()} (r = ${r.toFixed(2)}).`,
      );
      if (strength !== "no clear") out.push(r > 0 ? `When ${humanize(data.xName).toLowerCase()} goes up, ${humanize(data.yName).toLowerCase()} tends to go up too.` : `When ${humanize(data.xName).toLowerCase()} goes up, ${humanize(data.yName).toLowerCase()} tends to go down.`);
    }
  } else if (data.type === "histogram") {
    const fmt = formatOf(plan.profile.columns.find((c) => c.name === data.measure));
    const busiest = data.bins.reduce((best, b) => (b.count > best.count ? b : best), data.bins[0] ?? { from: 0, to: 0, count: 0 });
    const total = data.bins.reduce((s, b) => s + b.count, 0);
    out.push(`Middle value (median): ${formatValue(data.median, fmt)}.`);
    if (total) out.push(`Most common range: ${formatValue(busiest.from, fmt)} to ${formatValue(busiest.to, fmt)} (${pct((busiest.count / total) * 100)} of values).`);
  }
  return out.slice(0, 3);
}
