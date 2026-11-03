import { api } from "@/lib/api/client";

export type LeverType = "scale" | "shift" | "set" | "cap" | "floor" | "exclude" | "volume";

export interface SegmentSpec {
  column: string;
  op?: string;
  values?: (string | number)[];
  rank?: "top" | "bottom";
  n?: number;
}

export interface LeverSpec {
  type: LeverType;
  column?: string | null;
  percent?: number;
  amount?: number;
  value?: number;
  limit?: number;
  elasticity?: number;
  where?: SegmentSpec;
}

export interface ScenarioSpec {
  metric_name: string;
  metric_expression: string;
  fact_table: string;
  time_column: string | null;
  grain: string;
  filters: string | null;
  breakdown: string | null;
  levers: LeverSpec[];
  forecast_periods: number;
}

export interface LeverEffect {
  label: string;
  scenario: number | null;
  delta: number | null;
  delta_pct: number | null;
}

export interface SegmentImpact {
  segment: string;
  baseline: number | null;
  scenario: number | null;
  delta: number | null;
  delta_pct: number | null;
  share_of_change_pct: number | null;
  n: number;
}

export interface SweepReport {
  lever_index: number;
  lever: string;
  parameter: string;
  current: number;
  elasticity: number;
  baseline: number | null;
  points: { value: number; scenario: number | null; delta: number | null; delta_pct: number | null }[];
}

export interface ForecastPoint {
  period: string;
  yhat: number | null;
  lower: number | null;
  upper: number | null;
  scenario?: number | null;
  scenario_lower?: number | null;
  scenario_upper?: number | null;
}

export interface ForecastReport {
  status: string;
  kind: "forecast";
  spec: ScenarioSpec;
  metric: { name: string; kind: string };
  levers: (LeverSpec & { label: string })[];
  grain: string;
  horizon: number;
  history: { period: string; baseline: number | null; scenario: number | null; fitted: number | null }[];
  forecast: ForecastPoint[];
  model: string;
  params: Record<string, number>;
  backtest: { holdout_periods: number; mape: number | null; naive_mape: number | null; model: string } | null;
  scenario_ratio: number | null;
  summary: { aggregate: "total" | "average"; baseline: number | null; scenario?: number | null; delta_pct?: number | null };
  assumptions: string[];
  notes: string[];
  sql: { series: string };
}

export interface WhatIfReport {
  status: string;
  kind: "whatif";
  question: string;
  spec: ScenarioSpec;
  metric: { name: string; expression: string; kind: string };
  levers: (LeverSpec & { label: string })[];
  baseline: number | null;
  scenario: number | null;
  delta: number | null;
  delta_pct: number | null;
  rows: number;
  lever_effects: LeverEffect[];
  interaction: number | null;
  breakdown: { dimension: string; segments: number; unchanged_segments: number; rows: SegmentImpact[] } | null;
  assumptions: string[];
  notes: string[];
  sql: { totals: string; breakdown?: string };
  sensitivity?: SweepReport;
  forecast?: ForecastReport;
}

export const whatifApi = {
  ask: (connectionId: string, question: string) =>
    api.post<WhatIfReport | ForecastReport>("/whatif", { connection_id: connectionId, question }),
  rerun: (connectionId: string, spec: ScenarioSpec) =>
    api.post<WhatIfReport | ForecastReport>("/whatif", { connection_id: connectionId, spec }),
  sweep: (connectionId: string, spec: ScenarioSpec, leverIndex: number, elasticity?: number) =>
    api.post<SweepReport>("/whatif/sweep", {
      connection_id: connectionId,
      spec,
      lever_index: leverIndex,
      elasticity,
    }),
  forecast: (connectionId: string, spec: ScenarioSpec, horizon: number, grain?: string) =>
    api.post<ForecastReport>("/forecast", { connection_id: connectionId, spec, horizon, grain }),
};
