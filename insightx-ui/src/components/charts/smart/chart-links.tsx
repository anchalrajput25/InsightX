"use client";

// Cross-filtering: charts inside one <ChartLinkProvider> share a filter.
// Clicking "East" on one chart filters every other chart that has the same
// column. Charts outside a provider simply filter themselves.

import * as React from "react";

export interface LinkFilter {
  column: string;
  value: string;
  /** Chart that set the filter (it highlights instead of filtering). */
  source: string;
}

interface LinkContextValue {
  filter: LinkFilter | null;
  setFilter: (filter: LinkFilter | null) => void;
}

const LinkContext = React.createContext<LinkContextValue | null>(null);

export function ChartLinkProvider({ children }: { children: React.ReactNode }) {
  const [filter, setFilter] = React.useState<LinkFilter | null>(null);
  const value = React.useMemo(() => ({ filter, setFilter }), [filter]);
  return <LinkContext.Provider value={value}>{children}</LinkContext.Provider>;
}

/** Shared filter when inside a provider, otherwise a filter local to one chart. */
export function useChartLink(): LinkContextValue & { linked: boolean } {
  const shared = React.useContext(LinkContext);
  const [local, setLocal] = React.useState<LinkFilter | null>(null);
  if (shared) return { ...shared, linked: true };
  return { filter: local, setFilter: setLocal, linked: false };
}
