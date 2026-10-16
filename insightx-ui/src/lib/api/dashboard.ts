import { api } from "@/lib/api/client";
import type { DashboardOverview, SavedChart } from "@/lib/types";

export const dashboardApi = {
  overview: (connectionId: string, refresh = false) =>
    api.get<DashboardOverview>("/dashboard/overview", { connection_id: connectionId, refresh }),
  charts: (connectionId: string) => api.get<{ charts: SavedChart[] }>("/dashboard/charts", { connection_id: connectionId }),
  makeChart: (connectionId: string, request: string) =>
    api.post<SavedChart>("/dashboard/charts", { connection_id: connectionId, request }),
  removeChart: (id: string) => api.delete<{ deleted: number }>(`/dashboard/charts/${id}`),
};
