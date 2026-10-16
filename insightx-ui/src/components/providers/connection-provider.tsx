"use client";

import * as React from "react";
import { getConnection, setConnection as saveConnection } from "@/lib/session";
import type { Connection } from "@/lib/types";

interface ConnectionContextValue {
  connection: Connection | null;
  ready: boolean;
  setConnection: (connection: Connection | null) => void;
}

const ConnectionContext = React.createContext<ConnectionContextValue | null>(null);

export function ConnectionProvider({ children }: { children: React.ReactNode }) {
  const [connection, setState] = React.useState<Connection | null>(null);
  const [ready, setReady] = React.useState(false);

  React.useEffect(() => {
    setState(getConnection());
    setReady(true);
  }, []);

  const setConnection = React.useCallback((next: Connection | null) => {
    saveConnection(next);
    setState(next);
  }, []);

  return <ConnectionContext.Provider value={{ connection, ready, setConnection }}>{children}</ConnectionContext.Provider>;
}

export function useConnection(): ConnectionContextValue {
  const context = React.useContext(ConnectionContext);
  if (!context) throw new Error("useConnection must be used inside ConnectionProvider");
  return context;
}
