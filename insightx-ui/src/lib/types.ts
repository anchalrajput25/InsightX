// Types for the InsightX FastAPI responses this UI uses.
// They mirror backend/main.py, backend/dashboard.py, backend/alerting/api.py
// and backend/auth/routes.py. Fields the UI does not read are left out.

export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };
export type Row = Record<string, Json>;

// ---------------- auth ----------------
export interface User {
  id: number;
  email: string;
  name: string;
  created_at?: number;
  last_login_at?: number | null;
}
export interface AuthResponse {
  token: string;
  expires_at: number;
  user: User;
}
export interface MeResponse {
  user: User;
  questions_today: number;
  daily_limit: number;
}

// ---------------- connections ----------------
export type DbType = "postgresql" | "mysql" | "mssql";
export interface SchemaTable {
  table_name: string;
  row_count?: number;
  column_count?: number;
  description?: string;
}
export interface ConnectResponse {
  status: string;
  source_type: string;
  connection_id: string;
  message: string;
  schema?: { total_tables?: number; tables?: SchemaTable[]; relationships?: unknown[] };
  imported_tables?: string[];
  skipped_files?: { file_name: string; reason: string }[];
}
export interface UploadFile {
  file_name: string;
  importable?: boolean;
  error?: string | null;
  row_count?: number;
  column_count?: number;
  table_name?: string;
}
export interface UploadFolderResponse {
  status: string;
  session_id: string;
  files: UploadFile[];
  total_files: number;
  importable_files: number;
  skipped_files: { file_name: string; reason: string }[];
}
export interface Connection {
  id: string;
  label: string;
  kind: string;
  connectedAt: number;
  /** A small summary of the schema seen when connecting (for display only). */
  tables?: { name: string; rows?: number; columns?: number }[];
  links?: number;
}

// ---------------- questions ----------------
export interface ExecutionSummary {
  success: boolean;
  status: string;
  error: string | null;
  columns: string[];
  rows: Row[];
  row_count: number;
  truncated: boolean;
  max_rows: number | null;
  execution_time_ms: number;
}
export interface TrustReason {
  signal: string;
  effect: number;
  detail: string;
}
export interface Trust {
  level?: "high" | "medium" | "low" | string;
  score?: number;
  reasons?: TrustReason[];
}
export interface TraceSummary {
  id?: string;
  seconds?: number;
  llm_calls?: number;
  tokens?: number;
  db_queries?: number;
  cache?: string;
}
export interface ChatMessage {
  role: "user" | "assistant" | string;
  content: string;
}
export interface ChartData {
  kind?: string;
  rows?: Row[];
  columns?: string[];
  picked?: unknown;
  sql?: string;
}
export interface QueryResponse {
  status: string;
  answer: string;
  question: string;
  error: string | null;
  error_stage: string;
  clarification: string;
  sql: string;
  execution_result: ExecutionSummary;
  trust: Trust;
  claim_check: Record<string, unknown>;
  cache: { status?: string; [key: string]: unknown };
  trace: TraceSummary;
  chart_data: ChartData;
  follow_ups: string[];
  conversation_history: ChatMessage[];
  connection_id: string;
  thread_id: string;
  correction_attempts?: number;
}

// ---------------- investigation ----------------
export interface Headline {
  metric: string;
  baseline_period?: string;
  current_period?: string;
  baseline_label: string;
  current_label: string;
  baseline: number;
  current: number;
  change: number;
  change_pct: number | null;
  typical_change_pct?: number | null;
  unusual?: string;
  direction?: string;
  period_choice?: string;
}
export interface DriverSegment {
  segment: string;
  current: number;
  baseline: number;
  change_pct: number | null;
  contribution: number;
  share_of_change_pct?: number | null;
}
export interface Driver {
  dimension: string;
  leading_segment: string;
  top: DriverSegment[];
  total_change: number;
  score: number;
}
export interface Signal {
  signal: string;
  kind: string;
  overall_baseline: number;
  overall_current: number;
  overall_change: number;
  unit: string;
  scope?: string;
}
export interface Reason {
  kind: string;
  title: string;
  detail: string;
  likelihood?: string;
  strength?: number;
}
export interface InvestigationReport {
  question: string;
  status: string;
  plan: { metric: string; grain: string; fact_table: string; time_column: string; dimensions: string[] };
  trend: { period: string; value: number; n: number; partial: boolean }[];
  headline: Headline;
  volume_value?: {
    records_baseline?: number;
    records_current?: number;
    records_change_pct?: number | null;
    value_per_record_baseline?: number | null;
    value_per_record_current?: number | null;
    value_per_record_change_pct?: number | null;
    share_from_records_pct?: number | null;
    [key: string]: unknown;
  };
  drivers: Driver[];
  signals: Signal[];
  queries: { purpose: string; sql: string }[];
  reasons?: Reason[];
  ruled_out?: Reason[];
  facts?: string[];
  narrative?: {
    summary?: string;
    real_reason?: string;
    drivers?: string[];
    hypotheses?: { hypothesis: string; evidence?: string }[];
    actions?: { action: string; why?: string }[];
    next_steps?: string[];
  };
  trust?: Trust;
  trace?: TraceSummary;
  conversation_history?: ChatMessage[];
}

// ---------------- chats & history ----------------
export interface ChatListItem {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}
export interface HistoryItem {
  id: string;
  ts: number;
  kind: "question" | "investigation" | string;
  question: string;
  answer: string;
  sql: string;
  status: string;
  database: string;
  thread_id: string;
  detail?: Record<string, unknown>;
}
export interface HistoryPage {
  items: HistoryItem[];
  total: number;
  activity?: { ts: number; kind: string }[];
}

// ---------------- traces ----------------
export interface TraceListItem {
  id: string;
  ts: number;
  kind: string;
  question: string;
  status: string;
  seconds: number;
  llm_calls: number;
  tokens: number;
  db_queries: number;
  cache: string;
  error: string;
}
export interface TraceEvent {
  kind: "step" | "llm" | "db" | string;
  name: string;
  start: number;
  seconds: number;
  status?: string;
  sql?: string;
  model?: string;
  route?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  rows?: number;
  error?: string;
  [key: string]: unknown;
}
export interface TraceDetail extends TraceListItem {
  detail: { events?: TraceEvent[]; [key: string]: unknown };
}

// ---------------- AI usage ----------------
export interface UsageGroup {
  requests: number;
  ok: number;
  failed: number;
  cache_hits: number;
  questions: number;
  tokens: number;
  cost_usd: number;
  p50_latency_ms: number | null;
  route?: string;
  job?: string;
  user_id?: string;
}
export interface UsageSummary {
  days: number;
  totals: {
    questions: number;
    requests: number;
    successful: number;
    failed: number;
    cache_hits: number;
    cache_hit_rate: number | null;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
    calls_per_question: number | null;
    p50_latency_ms: number | null;
  };
  by_day: ({ day: string } & Record<string, number | string>)[];
  by_route: UsageGroup[];
  by_job: UsageGroup[];
  failures: { kind: string; count: number }[];
}
export interface ModelRoute {
  tier: string;
  position: number;
  route: string;
  provider: string;
  model: string;
  free: boolean;
  rpm_limit: number | null;
  rpd_limit: number | null;
  key_configured: boolean;
  blocked_reason: string;
  requests_today: number;
  cooldown_seconds: number;
  cooldown_reason: string;
  successes: number;
  failures: number;
  last_error: string;
}
export interface RoutesResponse {
  routes: ModelRoute[];
  settings: { user_daily_questions: number; cache_enabled: boolean; cache_ttl_hours: number };
}

// ---------------- dashboard ----------------
export interface OverviewChart {
  key: string;
  title: string;
  subtitle?: string;
  kind: string;
  sql?: string;
  columns: string[];
  rows: Row[];
}
export interface DashboardOverview {
  database?: string;
  tables: number;
  links: number;
  columns: number;
  rows: number;
  table_sizes: { table: string; rows: number }[];
  charts: OverviewChart[];
  built_at: number;
}
export interface SavedChart {
  id: string;
  request: string;
  kind: string;
  sql: string;
  answer: string;
  ts: number;
  columns: string[];
  rows: Row[];
  error?: string;
}

// ---------------- alerts ----------------
export type RuleType = "smart" | "limit" | "change" | "trend" | "stale";
export interface AlertRule {
  type: RuleType;
  direction?: "both" | "up" | "down";
  sensitivity?: "low" | "medium" | "high";
  op?: "above" | "below";
  value?: number;
  percent?: number;
  compare_to?: "previous" | "week" | "year";
  periods?: number;
  severity?: "auto" | "warning" | "critical";
  skip_unfinished?: boolean;
}
export interface Alert {
  id: string;
  database: string;
  connection_id: string;
  name: string;
  metric: string;
  sql: string;
  grain: string | null;
  fill_gaps: boolean;
  single: boolean;
  rule: AlertRule;
  check_minutes: number;
  channels: { webhook?: string; email?: string };
  status: "active" | "paused";
  created: number;
  next_check: number;
  last_checked: number | null;
  state: string;
  last_value: number | null;
  last_message: string;
  last_error: string;
  snooze_until: number;
}
export interface AlertSummary {
  open: number;
  critical: number;
  warning: number;
  active: number;
  paused: number;
  last_check: number | null;
  raised_24h: number;
  checker_running?: boolean;
}
export interface AlertsResponse {
  alerts: Alert[];
  summary: AlertSummary;
  checker_running: boolean;
  email_ready: boolean;
  check_minutes: number[];
}
export interface AlertEvent {
  id: string;
  alert_id: string;
  alert_name: string;
  database: string;
  ts: number;
  period: string;
  severity: "warning" | "critical" | string;
  title: string;
  message: string;
  value: number | null;
  expected: number | null;
  change_pct: number | null;
  status: "open" | "acknowledged" | "resolved";
  notified: string;
}
export interface Evaluation {
  status: string;
  fired: boolean;
  severity: string | null;
  value: number | null;
  period: string | null;
  expected: number | null;
  low: number | null;
  high: number | null;
  change_pct: number | null;
  title: string;
  message: string;
}
export interface BacktestPoint {
  t: string;
  v: number;
  expected: number | null;
  low: number | null;
  high: number | null;
  fired: boolean;
  severity: string | null;
}
export interface Backtest {
  grain: string | null;
  series: BacktestPoint[];
  fired: { t: string; period: string; severity: string; title: string }[];
  fired_count: number;
  checked: number;
}
export interface AlertPreview {
  name: string;
  sql: string;
  grain: string | null;
  single: boolean;
  fill_gaps: boolean;
  used_ai: boolean;
  rule: AlertRule;
  evaluation: Evaluation;
  backtest: Backtest;
}
export interface MetricSuggestion {
  key: string;
  name: string;
  description: string;
  grain: string;
  fill_gaps: boolean;
  sql: string;
}
