import { api } from "@/lib/api/client";
import type { ConnectResponse, DbType, UploadFolderResponse } from "@/lib/types";

export interface DatabaseForm {
  db_type: DbType;
  host: string;
  port?: number;
  database: string;
  username: string;
  password: string;
}

function toForm(values: Record<string, string | number | undefined>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") form.set(key, String(value));
  }
  return form;
}

export const connectionsApi = {
  connectDatabase: (values: DatabaseForm) =>
    api.postForm<ConnectResponse>("/connect-postgres", toForm({ ...values })),
  connectSqlite: (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return api.postForm<ConnectResponse>("/connect-sqlite", form);
  },
  uploadZip: (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return api.postForm<UploadFolderResponse>("/upload-folder", form);
  },
  importUpload: (sessionId: string, target: Omit<DatabaseForm, "db_type">) =>
    api.postForm<ConnectResponse>("/import-uploaded-files", toForm({ session_id: sessionId, ...target })),
  disconnect: (connectionId: string) => api.delete<{ status: string }>(`/disconnect-postgres/${connectionId}`),
};
