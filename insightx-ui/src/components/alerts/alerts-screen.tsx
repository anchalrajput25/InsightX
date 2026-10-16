"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, BellPlus, BellRing, Database, OctagonAlert, Radio } from "lucide-react";
import { AlertFeed } from "@/components/alerts/alert-feed";
import { MyAlerts } from "@/components/alerts/my-alerts";
import { NewAlertSheet } from "@/components/alerts/new-alert";
import { PageHeader } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConnection } from "@/components/providers/connection-provider";
import { alertsApi } from "@/lib/api/alerts";
import { ApiError } from "@/lib/api/client";
import { timeAgo } from "@/lib/format";

export function AlertsScreen() {
  const { connection, ready, setConnection } = useConnection();
  const id = connection?.id ?? "";
  const [creating, setCreating] = React.useState(false);
  const [tab, setTab] = React.useState("feed");

  const alerts = useQuery({
    queryKey: ["alerts", "list", id],
    queryFn: () => alertsApi.list(id),
    enabled: !!id,
    refetchInterval: 60_000,
  });
  const lost = alerts.error instanceof ApiError && alerts.error.lostConnection;
  React.useEffect(() => {
    if (lost) setConnection(null);
  }, [lost, setConnection]);

  if (!ready) return null;
  if (!connection) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-8 sm:px-8">
        <PageHeader title="Alerts" description="Watch numbers in your database and hear about it when they move." />
        <EmptyState
          icon={Database}
          title="Connect a database to set up alerts"
          description="Alerts you already made keep running on the server. Connect the same database to see and change them."
          action={
            <Button asChild>
              <Link href="/connect">
                <Database /> Connect a database
              </Link>
            </Button>
          }
          className="rounded-xl border border-dashed py-14"
        />
        <AlertFeed connectionId="" onCreate={() => undefined} />
      </div>
    );
  }

  const summary = alerts.data?.summary;
  const list = alerts.data?.alerts ?? [];
  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-8">
      <PageHeader
        title="Alerts"
        description={`Watching numbers in ${connection.label}.`}
        actions={
          <>
            {alerts.data && (
              <Badge variant={alerts.data.checker_running ? "success" : "warning"} className="h-7 px-2.5">
                <Radio /> {alerts.data.checker_running ? `Checker running · last check ${timeAgo(summary?.last_check)}` : "Checker stopped"}
              </Badge>
            )}
            <Button size="sm" onClick={() => setCreating(true)}>
              <BellPlus /> New alert
            </Button>
          </>
        }
      />

      {alerts.isPending ? (
        <CardsSkeleton count={4} />
      ) : alerts.isError ? (
        <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
      ) : (
        <div className="fx-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Open" value={summary?.open ?? 0} icon={BellRing} tone={summary?.open ? "warning" : "success"} hint={summary?.open ? "Need a look" : "All clear"} />
          <StatCard label="Critical" value={summary?.critical ?? 0} icon={OctagonAlert} tone={summary?.critical ? "critical" : "default"} />
          <StatCard label="Watching" value={summary?.active ?? 0} icon={Activity} hint={summary?.paused ? `${summary.paused} paused` : undefined} />
          <StatCard label="Raised in 24 hours" value={summary?.raised_24h ?? 0} icon={AlertTriangle} />
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4">
          <TabsTrigger value="feed">
            Feed
            {summary?.open ? <span className="tabular ml-1.5 rounded-full bg-critical/15 px-1.5 text-[10px] font-semibold text-critical">{summary.open}</span> : null}
          </TabsTrigger>
          <TabsTrigger value="mine">
            My alerts <span className="tabular ml-1.5 text-[11px] text-muted-foreground">{list.length}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="feed">
          <AlertFeed connectionId={id} onCreate={() => setCreating(true)} />
        </TabsContent>
        <TabsContent value="mine">
          {alerts.isPending ? (
            <div className="space-y-3">
              <Skeleton className="h-24 rounded-xl" />
              <Skeleton className="h-24 rounded-xl" />
            </div>
          ) : alerts.isError ? null : (
            <MyAlerts alerts={list} checkMinutes={alerts.data.check_minutes} onCreate={() => setCreating(true)} />
          )}
        </TabsContent>
      </Tabs>

      <NewAlertSheet
        open={creating}
        onOpenChange={setCreating}
        connectionId={id}
        emailReady={alerts.data?.email_ready ?? false}
        checkMinutes={alerts.data?.check_minutes ?? []}
      />
    </div>
  );
}
