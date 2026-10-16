import { api } from "@/lib/api/client";
import type { AuthResponse, MeResponse } from "@/lib/types";

export const authApi = {
  status: () => api.get<{ required: boolean }>("/auth/status"),
  login: (email: string, password: string) => api.post<AuthResponse>("/auth/login", { email, password }),
  signup: (email: string, name: string, password: string) =>
    api.post<AuthResponse>("/auth/signup", { email, name, password }),
  me: () => api.get<MeResponse>("/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    api.post<{ status: string }>("/auth/password", { current_password, new_password }),
  logoutEverywhere: () => api.post<{ status: string }>("/auth/logout-everywhere"),
};
