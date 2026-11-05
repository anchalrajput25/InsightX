"use client";

// "/" : signed-in people go straight to the app, as before; everyone else
// (signed out, or the backend not reachable yet) sees the landing page.

import * as React from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { LandingPage } from "@/components/landing/landing-page";

export function HomeGate() {
  const { state } = useAuth();
  const router = useRouter();

  React.useEffect(() => {
    if (state.phase === "ready") router.replace("/ask");
  }, [state.phase, router]);

  if (state.phase === "checking" || state.phase === "ready") return <div className="min-h-dvh bg-[#06070a]" />;
  return <LandingPage />;
}
