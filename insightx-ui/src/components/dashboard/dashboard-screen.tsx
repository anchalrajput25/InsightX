"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowUp, Database, LayoutDashboard, Link2, Loader2, RefreshCw, Rows3, Sparkles, Table2 } from "lucide-react";
import { axisProps, ChartTooltip, gridProps, shortLabel } from "@/components/charts/chart-kit";
import { ChartCard } from "@/components/common/chart-card";
import { PageHeader, SectionTitle } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useConnection } from "@/components/providers/connection-provider";
import { dashboardApi } from "@/lib/api/dashboard";
import { ApiError, errorMessage } from "@/lib/api/client";
import { formatNumber, stripEmoji, timeAgo } from "@/lib/format";
import type { SavedChart } from "@/lib/types";

function MakeChart({ connectionId }: { connectionId: string }) {
  const queryClient = useQueryClient();
  const [text, setText] = React.useState("");
  const make = useMutation({
    mutationFn: (request: string) => dashboardApi.makeChart(connectionId, request),
    onSuccess: (chart) => {
      queryClient.setQueryData<{ charts: SavedChart[] }>(["dashboard", "charts", connectionId], (old) => ({ charts: [chart, ...(old?.charts ?? [])] }));
      setText("");
      toast.success("Chart added to your dashboard");
    },
    onError: (error) => toast.error("Could not make that chart", { description: errorMessage(error) }),
  });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim()) make.mutate(text.trim());
      }}
      className="relative overflow-hidden rounded-xl border bg-card p-1.5 pl-4 focus-within:border-foreground/20"
    >
      <div aria-hidden className="pointer-events-none absolute -right-20 -top-20 size-48 rounded-full bg-primary/10 blur-3xl" />
      <div className="relative flex items-center gap-3">
        <Sparkles className="size-4 shrink-0 text-primary" />
        <label htmlFor="make-chart" className="sr-only">
          Describe a chart
        </label>
        <input
          id="make-chart"
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={make.isPending}
          placeholder="Make a chart: describe what to plot, for example sales by month as a line"
          className="h-10 min-w-0 flex-1 bg-transparent text-sm placeholder:text-muted-foreground/70 focus:outline-none"
          maxLength={1000}
        />
        <Button type="submit" size="sm" disabled={!text.trim() || make.isPending}>
          {make.isPending ? <Loader2 className="animate-spin" /> : <ArrowUp />}
          {make.isPending ? "Making…" : "Make chart"}
        </Button>
      </div>
    </form>
  );
}

function TableSizes({ sizes }: { sizes: { table: string; rows: number }[] }) {
  if (sizes.length === 0) return null;
  const data = sizes.slice(0, 12);
  return (
    <section className="rounded-xl border bg-card">
      <header className="px-4 pb-2 pt-3.5">
        <h3 className="text-sm font-medium">Largest tables</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">Rows per table</p>
      </header>
      <div className="px-4 pb-4" style={{ height: Math.max(200, data.length * 26 + 30) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 4 }} barCategoryGap={5}>
            <CartesianGrid {...gridProps} horizontal={false} vertical />
            <XAxis {...axisProps} type="number" tickFormatter={(v) => formatNumber(v)} />
            <YAxis {...axisProps} type="category" dataKey="table" width={120} tickFormatter={(v) => shortLabel(v, 18)} interval={0} />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.5 }} />
            <Bar dataKey="rows" name="Rows" fill="var(--chart-2)" radius={[0, 4, 4, 0]} maxBarSize={18} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function DashboardScreen() {
  const { connection, ready, setConnection } = useConnection();
  const queryClient = useQueryClient();
  const id = connection?.id ?? "";
  const [refreshing, setRefreshing] = React.useState(false);

  const overview = useQuery({
    queryKey: ["dashboard", "overview", id],
    queryFn: () => dashboardApi.overview(id),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });
  const charts = useQuery({
    queryKey: ["dashboard", "charts", id],
    queryFn: () => dashboardApi.charts(id),
    enabled: !!id,
  });
  const remove = useMutation({
    mutationFn: (chartId: string) => dashboardApi.removeChart(chartId),
    onSuccess: (_, chartId) => {
      queryClient.setQueryData<{ charts: SavedChart[] }>(["dashboard", "charts", id], (old) => ({ charts: (old?.charts ?? []).filter((chart) => chart.id !== chartId) }));
      toast.success("Chart removed");
    },
    onError: (error) => toast.error("Could not remove the chart", { description: errorMessage(error) }),
  });

  const lost = [overview.error, charts.error].some((error) => error instanceof ApiError && error.lostConnection);
  React.useEffect(() => {
    if (lost) setConnection(null);
  }, [lost, setConnection]);

  if (!ready) return null;
  if (!connection) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon={LayoutDashboard}
          title="No database connected"
          description="The dashboard shows an overview of the database you connect."
          action={
            <Button asChild>
              <Link href="/connect">
                <Database /> Connect a database
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  const data = overview.data;
  const saved = charts.data?.charts ?? [];
  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 px-4 py-8 sm:px-8">
      <PageHeader
        title="Dashboard"
        description={data ? `${data.database ?? connection.label} · built ${timeAgo(data.built_at)}` : connection.label}
        actions={
          <Button
            variant="outline"
            size="sm"
            disabled={refreshing || overview.isFetching}
            onClick={async () => {
              setRefreshing(true);
              try {
                queryClient.setQueryData(["dashboard", "overview", id], await dashboardApi.overview(id, true));
                await charts.refetch();
              } catch (error) {
                toast.error("Could not refresh", { description: errorMessage(error) });
              } finally {
                setRefreshing(false);
              }
            }}
          >
            <RefreshCw className={refreshing ? "animate-spin" : undefined} /> Refresh
          </Button>
        }
      />

      {overview.isPending ? (
        <CardsSkeleton count={4} />
      ) : overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : (
        <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Tables" value={formatNumber(data?.tables)} icon={Table2} />
          <StatCard label="Rows" value={formatNumber(data?.rows, { compactFrom: 1e6 })} icon={Rows3} />
          <StatCard label="Links between tables" value={formatNumber(data?.links)} icon={Link2} />
          <StatCard label="Columns" value={formatNumber(data?.columns)} icon={Database} />
        </div>
      )}

      <div className="space-y-4">
        <SectionTitle aside={saved.length ? <span className="text-xs text-muted-foreground">{saved.length} saved</span> : undefined}>Your charts</SectionTitle>
        <MakeChart connectionId={id} />
        {charts.isPending ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-72 rounded-xl" />
            <Skeleton className="h-72 rounded-xl" />
          </div>
        ) : charts.isError ? (
          <ErrorState error={charts.error} onRetry={() => void charts.refetch()} />
        ) : saved.length === 0 ? (
          <p className="rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">Charts you make appear here and update with your data.</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {saved.map((chart) => (
              <ChartCard
                key={chart.id}
                title={stripEmoji(chart.request)}
                subtitle={chart.answer ? stripEmoji(chart.answer) : undefined}
                columns={chart.columns}
                rows={chart.rows}
                kind={chart.kind}
                sql={chart.sql}
                error={chart.error}
                onDelete={() => remove.mutate(chart.id)}
              />
            ))}
          </div>
        )}
      </div>

      <div className="space-y-4">
        <SectionTitle>Overview</SectionTitle>
        {overview.isPending ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-72 rounded-xl" />
            ))}
          </div>
        ) : overview.isError ? null : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <TableSizes sizes={data?.table_sizes ?? []} />
            {(data?.charts ?? []).map((chart) => (
              <ChartCard key={chart.key} title={chart.title} subtitle={chart.subtitle} columns={chart.columns} rows={chart.rows} kind={chart.kind} sql={chart.sql} />
            ))}
            {(data?.charts ?? []).length === 0 && (
              <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground md:col-span-2">
                No dates or categories were found to chart automatically. Use Make a chart above.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
