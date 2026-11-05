import { api } from "@/lib/api/client";

/** Forgot password: ask for a reset link, then set a new password with it. */
export const passwordResetApi = {
  forgot: (email: string) => api.post<{ message: string }>("/auth/forgot-password", { email }),
  reset: (token: string, new_password: string) =>
    api.post<{ message: string }>("/auth/reset-password", { token, new_password }),
};
