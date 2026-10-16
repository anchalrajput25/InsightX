"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authApi } from "@/lib/api/auth";
import { getSession, setSession, type Session } from "@/lib/session";
import type { AuthResponse } from "@/lib/types";

type AuthState =
  | { phase: "checking" }
  | { phase: "offline"; message: string }
  | { phase: "signed-out"; required: true }
  | { phase: "ready"; required: boolean; session: Session };

interface AuthContextValue {
  state: AuthState;
  signedIn: (response: AuthResponse) => void;
  signOut: () => void;
  retry: () => void;
  displayName: string;
  email: string;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = React.useState<AuthState>({ phase: "checking" });

  const check = React.useCallback(async () => {
    setState({ phase: "checking" });
    try {
      const { required } = await authApi.status();
      const session = getSession();
      if (!required) {
        const local = session?.localName ? session : { localName: "web" };
        setSession(local);
        setState({ phase: "ready", required: false, session: local });
        return;
      }
      if (session?.token) {
        setState({ phase: "ready", required: true, session });
      } else {
        setSession(null);
        setState({ phase: "signed-out", required: true });
      }
    } catch {
      setState({ phase: "offline", message: "Cannot reach the InsightX backend. Start it, then try again." });
    }
  }, []);

  React.useEffect(() => {
    void check();
  }, [check]);

  const signOut = React.useCallback(() => {
    setSession(null);
    queryClient.clear();
    setState({ phase: "signed-out", required: true });
  }, [queryClient]);

  React.useEffect(() => {
    const onSignedOut = () => {
      if (getSession()?.token) signOut();
    };
    window.addEventListener("insightx:signed-out", onSignedOut);
    return () => window.removeEventListener("insightx:signed-out", onSignedOut);
  }, [signOut]);

  const signedIn = React.useCallback((response: AuthResponse) => {
    const session: Session = { token: response.token, expiresAt: response.expires_at, user: response.user };
    setSession(session);
    setState({ phase: "ready", required: true, session });
  }, []);

  const session = state.phase === "ready" ? state.session : undefined;
  const value: AuthContextValue = {
    state,
    signedIn,
    signOut,
    retry: () => void check(),
    displayName: session?.user?.name || session?.localName || "You",
    email: session?.user?.email ?? "",
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = React.useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
