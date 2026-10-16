import { api } from "@/lib/api/client";
import type { HistoryPage } from "@/lib/types";

export const historyApi = {
  list: (search: string, kind: string, limit: number, offset: number) =>
    api.get<HistoryPage>("/history", { search, kind, limit, offset }),
  remove: (id: string) => api.delete<{ deleted: number }>(`/history/${id}`),
  clear: () => api.delete<{ deleted: number }>("/history"),
};
