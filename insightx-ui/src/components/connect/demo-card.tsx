"use client";

// "Try the demo database": shown only when the server has a demo
// database set up (INSIGHTX_DEMO_DATABASE_URL). One click connects it;
// its address and password stay on the server.

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api/client";
import type { ConnectResponse } from "@/lib/types";

interface DemoInfo {
  available: boolean;
  label: string;
}

export function DemoCard({ onConnected }: { onConnected: (response: ConnectResponse, label: string) => void }) {
  const info = useQuery({
    queryKey: ["demo-database"],
    queryFn: () => api.get<DemoInfo>("/demo-database"),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  if (!info.data?.available) return null;
  const label = info.data.label;

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<ConnectResponse & { label?: string }>("/connect-demo");
      onConnected(response, response.label ?? label);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="border-primary/40">
      <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <div className="rounded-md bg-primary/10 p-2 text-primary">
            <Database className="size-5" />
          </div>
          <div>
            <div className="font-medium">Try the demo database</div>
            <p className="text-sm text-muted-foreground">
              {label}: 108 tables of sales, film rentals, music and trading data. Read-only, ready in one click.
            </p>
            {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
          </div>
        </div>
        <Button onClick={connect} disabled={busy} className="shrink-0">
          {busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}
          {busy ? "Connecting..." : "Use demo data"}
        </Button>
      </CardContent>
    </Card>
  );
}
