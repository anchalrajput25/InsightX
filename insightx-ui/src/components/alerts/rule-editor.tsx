"use client";

import { Activity, Clock3, Gauge, Ruler, TrendingDown, Zap } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { COMPARE, DIRECTIONS, RULE_TYPES, SEVERITY, unitLabel } from "@/components/alerts/alert-utils";
import type { AlertRule, RuleType } from "@/lib/types";
import { cn } from "@/lib/utils";

const RULE_ICONS: Record<RuleType, typeof Gauge> = { smart: Activity, limit: Ruler, change: Zap, trend: TrendingDown, stale: Clock3 };

function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1 rounded-lg bg-muted/60 p-0.5">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-7 flex-1 whitespace-nowrap rounded-md px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
              value === option.value && "bg-card text-foreground shadow-sm",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function NumberField({ id, label, value, onChange, min, max, step }: { id: string; label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        value={Number.isFinite(value) ? value : ""}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))}
      />
    </div>
  );
}

export function RuleEditor({ rule, onChange, grain, single, idPrefix }: { rule: AlertRule; onChange: (rule: AlertRule) => void; grain: string | null; single: boolean; idPrefix: string }) {
  const set = (patch: Partial<AlertRule>) => onChange({ ...rule, ...patch });
  const unit = unitLabel(single ? null : grain);
  const types = RULE_TYPES.filter((type) => !(single && type.value === "stale"));
  const directions = (Object.keys(DIRECTIONS) as (keyof typeof DIRECTIONS)[]).map((value) => ({ value, label: DIRECTIONS[value] }));
  const help = RULE_TYPES.find((type) => type.value === rule.type)?.help;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Label>When should I warn you?</Label>
        <div role="radiogroup" aria-label="Alert type" className="grid gap-2 sm:grid-cols-2">
          {types.map((type) => {
            const Icon = RULE_ICONS[type.value];
            const active = rule.type === type.value;
            return (
              <button
                key={type.value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => set({ type: type.value, direction: type.value === "trend" ? "down" : rule.direction === "down" && rule.type === "trend" ? "both" : rule.direction })}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-left text-[13px] transition-colors hover:border-foreground/20",
                  active && "border-primary/60 bg-primary/[0.06]",
                )}
              >
                <Icon className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
                <span className="font-medium">{type.label}</span>
              </button>
            );
          })}
        </div>
        {help && <p className="text-xs leading-relaxed text-muted-foreground">{help}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {rule.type === "smart" && (
          <>
            <Segmented
              label="Sensitivity"
              value={rule.sensitivity ?? "medium"}
              options={[
                { value: "low", label: "Low" },
                { value: "medium", label: "Medium" },
                { value: "high", label: "High" },
              ]}
              onChange={(sensitivity) => set({ sensitivity })}
            />
            <Segmented label="Warn about" value={rule.direction ?? "both"} options={directions} onChange={(direction) => set({ direction })} />
          </>
        )}
        {rule.type === "limit" && (
          <>
            <Segmented
              label="When it goes"
              value={rule.op ?? "above"}
              options={[
                { value: "above", label: "Above" },
                { value: "below", label: "Below" },
              ]}
              onChange={(op) => set({ op })}
            />
            <NumberField id={`${idPrefix}-value`} label="Limit" value={rule.value ?? 0} onChange={(value) => set({ value })} />
          </>
        )}
        {rule.type === "change" && (
          <>
            <NumberField id={`${idPrefix}-percent`} label="By more than (%)" value={rule.percent ?? 30} min={1} max={10000} step={5} onChange={(percent) => set({ percent })} />
            <div className="space-y-1.5">
              <Label htmlFor={`${idPrefix}-compare`}>Compared with</Label>
              <Select value={single ? "previous" : (rule.compare_to ?? "previous")} onValueChange={(value) => set({ compare_to: value as AlertRule["compare_to"] })}>
                <SelectTrigger id={`${idPrefix}-compare`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(COMPARE) as (keyof typeof COMPARE)[])
                    .filter((key) => !single || key === "previous")
                    .map((key) => (
                      <SelectItem key={key} value={key}>
                        {COMPARE[key]}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div className="sm:col-span-2">
              <Segmented label="Warn about" value={rule.direction ?? "both"} options={directions} onChange={(direction) => set({ direction })} />
            </div>
          </>
        )}
        {rule.type === "trend" && (
          <>
            <NumberField id={`${idPrefix}-periods`} label={`How many ${unit}s in a row`} value={rule.periods ?? 3} min={2} max={60} onChange={(periods) => set({ periods })} />
            <Segmented
              label="Direction"
              value={rule.direction ?? "down"}
              options={[
                { value: "down", label: "Falling" },
                { value: "up", label: "Rising" },
                { value: "both", label: "Either" },
              ]}
              onChange={(direction) => set({ direction })}
            />
          </>
        )}
        {rule.type === "stale" && (
          <NumberField id={`${idPrefix}-gap`} label={`No new data for how many ${unit}s`} value={rule.periods ?? 2} min={1} max={365} onChange={(periods) => set({ periods })} />
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-severity`}>How serious</Label>
          <Select value={rule.severity ?? "auto"} onValueChange={(value) => set({ severity: value as AlertRule["severity"] })}>
            <SelectTrigger id={`${idPrefix}-severity`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SEVERITY) as (keyof typeof SEVERITY)[]).map((key) => (
                <SelectItem key={key} value={key}>
                  {SEVERITY[key]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {rule.type !== "stale" && !single && (
          <label htmlFor={`${idPrefix}-skip`} className="flex items-start gap-3 rounded-lg border px-3 py-2.5 sm:mt-6">
            <Switch id={`${idPrefix}-skip`} checked={rule.skip_unfinished ?? true} onCheckedChange={(skip_unfinished) => set({ skip_unfinished })} className="mt-0.5" />
            <span className="text-[13px]">
              Ignore the period that is not over yet
              <span className="block text-xs text-muted-foreground">Today&apos;s number is still growing, so it would look low.</span>
            </span>
          </label>
        )}
      </div>
    </div>
  );
}

/** Numbers typed as NaN (empty boxes) are sent as the backend default. */
export function cleanRule(rule: AlertRule): AlertRule {
  const out: AlertRule = { ...rule };
  for (const key of ["value", "percent", "periods"] as const) {
    const value = out[key];
    if (value === undefined || !Number.isFinite(value)) delete out[key];
  }
  return out;
}
