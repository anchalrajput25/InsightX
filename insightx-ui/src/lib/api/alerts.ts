import { api } from "@/lib/api/client";
import type {
  Alert,
  AlertEvent,
  AlertPreview,
  AlertRule,
  AlertsResponse,
  AlertSummary,
  Backtest,
  Evaluation,
  MetricSuggestion,
} from "@/lib/types";

export interface MetricInput {
  connection_id: string;
  metric_text?: string;
  sql?: string;
  name?: string;
  fill_gaps?: boolean;
  rule: AlertRule;
}

export interface CreateAlertInput extends MetricInput {
  check_minutes: number;
  webhook?: string;
  email?: string;
}

export interface AlertChange {
  status?: "active" | "paused";
  snooze_hours?: number;
  rule?: Partial<AlertRule>;
  check_minutes?: number;
  name?: string;
  webhook?: string;
  email?: string;
}

export const alertsApi = {
  list: (connectionId: string) => api.get<AlertsResponse>("/alerts", { connection_id: connectionId }),
  summary: (connectionId?: string) => api.get<AlertSummary>("/alerts/summary", { connection_id: connectionId }),
  events: (status: "open" | "all" | "resolved", connectionId?: string) =>
    api.get<{ events: AlertEvent[] }>("/alerts/events", { status, connection_id: connectionId, limit: 200 }),
  eventAction: (id: string, action: "acknowledge" | "resolve" | "reopen") =>
    api.post<{ status: string }>(`/alerts/events/${id}`, { action }),
  suggestions: (connectionId: string, grain: string) =>
    api.get<{ suggestions: MetricSuggestion[] }>("/alerts/suggestions", { connection_id: connectionId, grain }),
  preview: (input: MetricInput) => api.post<AlertPreview>("/alerts/preview", input),
  create: (input: CreateAlertInput) => api.post<{ alert: Alert; first_check: Evaluation }>("/alerts", input),
  chart: (id: string) => api.get<{ alert_id: string; single: boolean; evaluation: Evaluation; backtest: Backtest }>(`/alerts/${id}/chart`),
  check: (id: string) => api.post<{ result: Evaluation; alert: Alert }>(`/alerts/${id}/check`),
  change: (id: string, change: AlertChange) => api.patch<Alert>(`/alerts/${id}`, change),
  remove: (id: string) => api.delete<{ deleted: number }>(`/alerts/${id}`),
  testNotification: (webhook: string, email: string) =>
    api.post<{ results: string[] }>("/alerts/test-notification", { webhook, email }),
};
