import { api } from "@/lib/api/client";
import type { RoutesResponse, TraceDetail, TraceListItem, UsageSummary } from "@/lib/types";

export const monitoringApi = {
  traces: (limit = 100) => api.get<{ traces: TraceListItem[] }>("/traces", { limit }),
  trace: (id: string) => api.get<TraceDetail>(`/traces/${id}`),
  usage: (days: number) => api.get<UsageSummary>("/ai/usage", { days }),
  routes: () => api.get<RoutesResponse>("/ai/routes"),
};
