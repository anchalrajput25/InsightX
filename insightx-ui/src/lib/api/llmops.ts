// Calls for the LLMOps pages: Monitor, Evals, Hallucination, Quality, Routing.
// Backend: backend/llmops/api.py (/llmops/...).

import { api } from "@/lib/api/client";

export type Risk = "none" | "low" | "medium" | "high";
export type MonitorWindow = "1h" | "24h" | "7d" | "30d";

// ---------------- monitor ----------------
export interface MonitorKpis {
  questions: number;
  success_rate: number | null;
  p50_seconds: number | null;
  p95_seconds: number | null;
  ai_calls: number;
  ai_error_rate: number | null;
  cache_hit_rate: number | null;
  tokens: number;
  cost_usd: number;
  checked_answers: number;
  hallucination_rate: number | null;
  mean_faithfulness: number | null;
  thumbs_up_rate: number | null;
  rated_answers: number;
}

export interface MonitorPoint {
  t: number;
  questions: number;
  errors: number;
  p50_seconds: number | null;
  p95_seconds: number | null;
  ai_calls: number;
  ai_errors: number;
  tokens: number;
  cache_hits: number;
  flagged: number;
}

export interface RouteHealth {
  route: string;
  provider: string;
  tier: string;
  calls: number;
  success_rate: number | null;
  p50_seconds: number | null;
  p95_seconds: number | null;
  tokens: number;
  errors: Record<string, number>;
  state: "healthy" | "degraded" | "failing" | "blocked" | "no_key" | "idle";
  blocked_reason: string;
  requests_today: number | null;
  rpd_limit: number | null;
  key_configured: boolean | null;
}

export interface SloRow {
  key: string;
  label: string;
  value: number | null;
  target: number;
  met: boolean | null;
  higher_is_better: boolean;
  unit: "ratio" | "seconds";
  budget_used?: number;
  failures?: number;
  allowed_failures?: number;
}

export interface Incident {
  t: number;
  metric: string;
  label: string;
  value: number;
  usual: number;
  limit: number;
}

export interface MonitorOverview {
  window: MonitorWindow;
  generated_at: number;
  kpis: MonitorKpis;
  previous: MonitorKpis;
  series: MonitorPoint[];
  routes: RouteHealth[];
  stages: { stage: string; count: number; mean_seconds: number; p95_seconds: number | null }[];
  slo: SloRow[];
  incidents: Incident[];
  failures: { id: string; ts: number; question: string; status: string; error: string; seconds: number | null }[];
}

export interface SloTargets {
  success_rate: number;
  p95_seconds: number;
  ai_error_rate: number;
  hallucination_rate: number;
}

// ---------------- answers ----------------
export interface Finding {
  kind: string;
  weight: number;
  detail: string;
  text: string;
}

export interface AnswerChecks {
  score: number | null;
  risk: Risk;
  hallucinated: boolean;
  findings: Finding[];
  claims: { numbers: { text: string; verified: boolean }[]; values: { text: string; verified: boolean }[] };
  applicable: boolean;
}

export interface JudgeVerdict {
  faithful?: boolean;
  score?: number;
  unsupported_claims?: string[];
  explanation?: string;
  seconds?: number;
  at?: number;
}

export interface AnswerCheck {
  id: string;
  ts: number;
  day: string;
  trace_id: string;
  thread_id: string;
  question: string;
  status: string;
  sql: string;
  answer: string;
  row_count: number | null;
  seconds: number | null;
  trust_level: string;
  trust_score: number | null;
  difficulty: string;
  faithfulness: number | null;
  risk: Risk;
  hallucinated: number;
  checks: AnswerChecks;
  preview?: { columns: string[]; rows: Record<string, unknown>[] };
  judge: JudgeVerdict;
  rating: number;
  note: string;
  review: "open" | "fixed" | "dismissed" | "promoted";
  fixed_sql: string;
}

export type AnswerFilter = "all" | "flagged" | "hallucinated" | "down" | "up" | "review";

export interface RateRow {
  checked: number;
  hallucinated: number;
}

export interface HallucinationSummary {
  checked: number;
  hallucinated: number;
  rate: number | null;
  mean_faithfulness: number | null;
  risk: Partial<Record<Risk, number>>;
  kinds: Record<string, number>;
  by_day: { day: string; checked: number; hallucinated: number; rate: number | null; mean_faithfulness: number | null }[];
  by_difficulty: Record<string, RateRow>;
  by_trust: Record<string, RateRow>;
  judged: number;
  judge_agreement: number | null;
  flagged: AnswerCheck[];
  weights: Record<string, number>;
}

export interface QualitySummary {
  answers: number;
  rated: number;
  up: number;
  down: number;
  satisfaction: number | null;
  success_rate: number | null;
  review_states: Record<string, number>;
  review_queue: AnswerCheck[];
  by_day: { day: string; answers: number; up: number; down: number; ok: number; mean_trust: number | null; quality: number | null }[];
  suggestions: { kind: string; count: number; text: string }[];
  promoted: number;
}

export interface SqlTest {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  same_as_answer: boolean | null;
}

// ---------------- evals ----------------
export interface GateCheck {
  metric: string;
  value: number | null;
  target: number;
  passed: boolean | null;
}

export interface Gate {
  passed: boolean;
  checks: GateCheck[];
}

export interface GateTargets {
  execution_accuracy: number;
  answer_faithfulness: number;
  safety_pass_rate: number;
  latency_p95_s: number;
}

export interface ReportItem {
  id: string;
  scope: "shared" | "mine";
  name: string;
  golden_set: string;
  database: string;
  run_at: string;
  modified: number;
  case_count: number;
  variants: string[];
  stopped: string;
  execution_accuracy: number | null;
  answer_faithfulness: number | null;
  latency_p95_s: number | null;
  gate: Gate;
}

export interface EvalCase {
  id: string;
  question: string;
  difficulty: string;
  expect: string;
  passed: boolean;
  status: string;
  diagnosis: string;
  reason: string;
  predicted_sql: string;
  expected_sql: string;
  latency_s: number;
  trust_level: string;
  answer_faithful: boolean | null;
  models: Record<string, number>;
  variant: string;
  repeat: number;
  tags: string[];
}

export interface EvalSummary {
  execution_accuracy?: number | null;
  execution_accuracy_ci?: [number, number] | null;
  strict_accuracy?: number | null;
  answer_faithfulness?: number | null;
  safety_pass_rate?: number | null;
  abstention_rate?: number | null;
  mean_retrieval_recall?: number | null;
  correction_rate?: number | null;
  latency_p50_s?: number | null;
  latency_p95_s?: number | null;
  by_difficulty?: Record<string, { cases?: number; accuracy?: number | null; [key: string]: unknown }>;
  by_tag?: Record<string, { cases?: number; accuracy?: number | null; [key: string]: unknown }>;
  diagnoses?: Record<string, number>;
  stage_mean_s?: Record<string, number>;
  [key: string]: unknown;
}

export interface EvalReport {
  id: string;
  golden_set: string;
  description: string;
  database: string;
  run_at: string;
  duration_s: number;
  repeats: number;
  case_count: number;
  stopped: string;
  summary: EvalSummary;
  cases: EvalCase[];
  variants: { name: string; label: string; summary: EvalSummary; partial: boolean }[];
  gate: Gate;
}

export interface CompareResult {
  base: { id: string; golden_set: string; run_at: string };
  other: { id: string; golden_set: string; run_at: string };
  metrics: { key: string; label: string; base: number | null; other: number | null; delta: number | null; improved: boolean | null }[];
  regressions: { id: string; question: string; difficulty: string; base: number; other: number }[];
  fixes: { id: string; question: string; difficulty: string; base: number; other: number }[];
  only_in_base: string[];
  only_in_other: string[];
}

export interface GoldenItem {
  id: string;
  scope: "builtin" | "mine";
  name: string;
  description?: string;
  cases?: number;
  verified?: number;
  by_difficulty?: Record<string, number>;
  tags?: string[];
  error?: string;
}

export interface EvalJob {
  id: string;
  state: "running" | "finished" | "failed" | "stopped";
  started: number;
  ended: number | null;
  golden_id: string;
  database: string;
  options: Record<string, unknown>;
  exit_code?: number;
  progress: {
    total: number | null;
    done: number;
    passed: number;
    recent: { id: string; passed: boolean; line: string }[];
    tail: string[];
  };
  report_id: string | null;
}

export interface RunInput {
  connection_id: string;
  golden_id: string;
  repeats: number;
  workers: number;
  difficulty?: string;
  ablation?: boolean;
  max_cost?: number | null;
}

// ---------------- routing ----------------
export type RoutingMode = "off" | "shadow" | "on";
export type Level = "easy" | "medium" | "hard";

export interface ScoredRoute {
  route: string;
  score: number;
  position: number;
  parts: { accuracy: number; success: number; speed: number; headroom: number };
  blocked: boolean;
  calls_7d: number;
  eval_cases: number;
}

export interface RoutingOverview {
  mode: RoutingMode;
  summary: { mode: RoutingMode; decisions: number; changed: number; by_difficulty: Record<string, number>; first_choice: Record<string, number>; installed: boolean };
  weights: Record<Level, Record<string, number>>;
  preview: Record<Level, { tier: string; default: string[]; ranked: ScoredRoute[]; fallback_tiers: string[] }[]>;
  stats: Record<string, { calls: number; success_rate: number | null; p50_seconds: number | null }>;
  accuracy: Record<string, { cases: number; accuracy: number }>;
}

export interface RoutingDecision {
  ts: number;
  tier: string;
  difficulty: string;
  mode: RoutingMode;
  default: string[];
  chosen: string[];
  changed: boolean;
  scores: ScoredRoute[];
}

export const llmopsApi = {
  monitor: (window: MonitorWindow) => api.get<MonitorOverview>("/llmops/monitor", { window }),
  slo: () => api.get<SloTargets>("/llmops/slo"),
  saveSlo: (targets: Partial<SloTargets>) => api.put<SloTargets>("/llmops/slo", targets),

  answers: (only: AnswerFilter, search = "", days = 30) =>
    api.get<{ answers: AnswerCheck[] }>("/llmops/answers", { only, search, days, limit: 300 }),
  answer: (id: string) => api.get<AnswerCheck>(`/llmops/answers/${id}`),
  feedback: (id: string, rating: number, note = "") => api.post<AnswerCheck>(`/llmops/answers/${id}/feedback`, { rating, note }),
  review: (id: string, review: "open" | "fixed" | "dismissed") => api.post<AnswerCheck>(`/llmops/answers/${id}/review`, { review }),
  judge: (id: string) => api.post<AnswerCheck>(`/llmops/answers/${id}/judge`),
  testSql: (id: string, connectionId: string, sql: string) =>
    api.post<SqlTest>(`/llmops/answers/${id}/test-sql`, { connection_id: connectionId, sql }),
  promote: (id: string, connectionId: string, sql: string, note: string) =>
    api.post<{ golden_id: string; case_id: string; updated: boolean; answer: AnswerCheck }>(`/llmops/answers/${id}/promote`, {
      connection_id: connectionId,
      sql,
      note,
    }),
  hallucination: (days: number) => api.get<HallucinationSummary>("/llmops/hallucination", { days }),
  quality: (days: number) => api.get<QualitySummary>("/llmops/quality", { days }),

  reports: () => api.get<{ reports: ReportItem[]; gate: GateTargets }>("/llmops/evals/reports"),
  report: (id: string) => api.get<EvalReport>("/llmops/evals/report", { id }),
  compare: (base: string, other: string) => api.get<CompareResult>("/llmops/evals/compare", { base, other }),
  saveGate: (targets: Partial<GateTargets>) => api.put<GateTargets>("/llmops/evals/gate", targets),
  golden: () => api.get<{ golden: GoldenItem[] }>("/llmops/evals/golden"),
  goldenCases: (id: string) =>
    api.get<{ id: string; name: string; description: string; cases: { id: string; question: string; expected_sql?: string; difficulty: string; tags?: string[]; notes?: string }[] }>(
      "/llmops/evals/golden/cases",
      { id },
    ),
  runs: () => api.get<{ runs: EvalJob[] }>("/llmops/evals/runs"),
  startRun: (input: RunInput) => api.post<EvalJob>("/llmops/evals/runs", input),
  stopRun: (id: string) => api.post<EvalJob>(`/llmops/evals/runs/${id}/stop`),

  routing: () => api.get<RoutingOverview>("/llmops/routing"),
  setRouting: (mode: RoutingMode) => api.put<{ mode: RoutingMode }>("/llmops/routing", { mode }),
  decisions: () => api.get<{ decisions: RoutingDecision[] }>("/llmops/routing/decisions", { limit: 100 }),
  classify: (question: string) =>
    api.get<{ level: Level; score: number; signals: string[]; words: number }>("/llmops/routing/classify", { question }),
};
