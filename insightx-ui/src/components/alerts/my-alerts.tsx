"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BellOff, BellPlus, ChevronDown, Loader2, MoreHorizontal, Pause, Pencil, Play, RefreshCw, Trash2 } from "lucide-react";
import { AlertChart } from "@/components/alerts/alert-chart";
import { describeRule, FREQUENCY, STATE } from "@/components/alerts/alert-utils";
import { cleanRule, RuleEditor } from "@/components/alerts/rule-editor";
import { Verdict } from "@/components/alerts/verdict";
import { CodeBlock } from "@/components/common/code-block";
import { EmptyState } from "@/components/common/states";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { alertsApi, type AlertChange } from "@/lib/api/alerts";
import { errorMessage } from "@/lib/api/client";
import { formatNumber, stripEmoji, timeAgo } from "@/lib/format";
import type { Alert, AlertRule } from "@/lib/types";
import { cn } from "@/lib/utils";

function AlertDetail({ alert }: { alert: Alert }) {
  const chart = useQuery({ queryKey: ["alerts", "chart", alert.id, alert.last_checked], queryFn: () => alertsApi.chart(alert.id) });
  if (chart.isPending) return <Skeleton className="h-56 w-full" />;
  if (chart.isError) return <p className="text-sm text-critical">{errorMessage(chart.error)}</p>;
  return (
    <div className="space-y-4">
      <Verdict evaluation={chart.data.evaluation} />
      <AlertChart backtest={chart.data.backtest} rule={alert.rule} />
      <p className="text-xs text-muted-foreground">
        On this history the rule would have alerted {chart.data.backtest.fired_count} time{chart.data.backtest.fired_count === 1 ? "" : "s"} in {chart.data.backtest.checked} checks.
      </p>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">SQL</summary>
        <CodeBlock code={alert.sql} className="mt-2" />
      </details>
    </div>
  );
}

function EditDialog({ alert, open, onOpenChange, checkMinutes, onSave }: { alert: Alert; open: boolean; onOpenChange: (open: boolean) => void; checkMinutes: number[]; onSave: (change: AlertChange) => Promise<unknown> }) {
  const [rule, setRule] = React.useState<AlertRule>(alert.rule);
  const [name, setName] = React.useState(alert.name);
  const [minutes, setMinutes] = React.useState(alert.check_minutes);
  const [webhook, setWebhook] = React.useState(alert.channels.webhook ?? "");
  const [email, setEmail] = React.useState(alert.channels.email ?? "");
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => {
    if (open) {
      setRule(alert.rule);
      setName(alert.name);
      setMinutes(alert.check_minutes);
      setWebhook(alert.channels.webhook ?? "");
      setEmail(alert.channels.email ?? "");
    }
  }, [open, alert]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit alert</DialogTitle>
        </DialogHeader>
        <div className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`edit-${alert.id}-name`}>Name</Label>
              <Input id={`edit-${alert.id}-name`} value={name} onChange={(event) => setName(event.target.value)} maxLength={300} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`edit-${alert.id}-every`}>Check</Label>
              <Select value={String(minutes)} onValueChange={(value) => setMinutes(Number(value))}>
                <SelectTrigger id={`edit-${alert.id}-every`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {checkMinutes.map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {FREQUENCY[value] ?? `Every ${value} minutes`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <RuleEditor rule={rule} onChange={setRule} grain={alert.grain} single={alert.single} idPrefix={`edit-${alert.id}`} />
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`edit-${alert.id}-hook`}>Web hook</Label>
              <Input id={`edit-${alert.id}-hook`} type="url" value={webhook} onChange={(event) => setWebhook(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`edit-${alert.id}-mail`}>E-mail</Label>
              <Input id={`edit-${alert.id}-mail`} type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={saving}
            onClick={async () => {
              setSaving(true);
              try {
                await onSave({ name, check_minutes: minutes, rule: cleanRule(rule), webhook, email });
                onOpenChange(false);
              } catch {
                // the toast from onSave explains it
              } finally {
                setSaving(false);
              }
            }}
          >
            {saving && <Loader2 className="animate-spin" />} Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AlertRow({ alert, checkMinutes }: { alert: Alert; checkMinutes: number[] }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["alerts"] });

  const change = useMutation({
    mutationFn: (body: AlertChange) => alertsApi.change(alert.id, body),
    onSuccess: () => {
      refresh();
      toast.success("Alert updated");
    },
    onError: (error) => toast.error("Could not update the alert", { description: errorMessage(error) }),
  });
  const check = useMutation({
    mutationFn: () => alertsApi.check(alert.id),
    onSuccess: ({ result }) => {
      refresh();
      toast.message(result.fired ? `Alert: ${stripEmoji(result.title)}` : result.status === "ok" ? "Checked: normal" : `Checked: ${stripEmoji(result.title) || result.status}`);
    },
    onError: (error) => toast.error("Check failed", { description: errorMessage(error) }),
  });
  const remove = useMutation({
    mutationFn: () => alertsApi.remove(alert.id),
    onSuccess: () => {
      refresh();
      toast.success("Alert deleted");
    },
    onError: (error) => toast.error("Could not delete the alert", { description: errorMessage(error) }),
  });

  const paused = alert.status === "paused";
  const snoozed = alert.snooze_until * 1000 > Date.now();
  const state = STATE[alert.state] ?? { label: alert.state, tone: "outline" as const };

  return (
    <li className="rounded-xl border bg-card">
      <div className="flex items-start gap-3 p-4">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} chart for ${alert.name}`}
          className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
        </button>
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => setOpen((value) => !value)} className="text-left text-[13.5px] font-medium hover:underline">
              {alert.name}
            </button>
            <Badge variant={paused ? "outline" : state.tone}>{paused ? "Paused" : state.label}</Badge>
            {snoozed && (
              <Badge variant="outline">
                <BellOff /> Snoozed {timeAgo(alert.snooze_until).replace(/^in /, "for ")}
              </Badge>
            )}
          </div>
          <p className="text-[13px] text-muted-foreground">{describeRule(alert.rule, alert.single ? null : alert.grain)}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {alert.last_value !== null && <span className="tabular">Latest {formatNumber(alert.last_value)}</span>}
            <span>Checked {timeAgo(alert.last_checked)}</span>
            {!paused && <span>Next {timeAgo(alert.next_check)}</span>}
            <span>{FREQUENCY[alert.check_minutes] ?? `Every ${alert.check_minutes} minutes`}</span>
            {alert.channels.webhook && <span>Web hook</span>}
            {alert.channels.email && <span>{alert.channels.email}</span>}
          </div>
          {alert.last_error && <p className="text-xs text-critical">{alert.last_error}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="xs" onClick={() => check.mutate()} disabled={check.isPending} className="hidden sm:inline-flex">
            <RefreshCw className={cn(check.isPending && "animate-spin")} /> Check now
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Options for ${alert.name}`}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuItem className="sm:hidden" onSelect={() => check.mutate()}>
                <RefreshCw /> Check now
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <Pencil /> Edit
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => change.mutate({ status: paused ? "active" : "paused" })}>
                {paused ? <Play /> : <Pause />} {paused ? "Resume" : "Pause"}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Snooze notifications</DropdownMenuLabel>
              {[1, 8, 24, 168].map((hours) => (
                <DropdownMenuItem key={hours} onSelect={() => change.mutate({ snooze_hours: hours })}>
                  <BellOff /> {hours < 24 ? `${hours} hour${hours === 1 ? "" : "s"}` : hours === 24 ? "1 day" : "1 week"}
                </DropdownMenuItem>
              ))}
              {snoozed && (
                <DropdownMenuItem onSelect={() => change.mutate({ snooze_hours: 0 })}>
                  <BellPlus /> Stop snoozing
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem className="text-critical focus:text-critical" onSelect={() => setDeleting(true)}>
                <Trash2 /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {open && (
        <div className="border-t px-4 py-4 sm:pl-13">
          <AlertDetail alert={alert} />
        </div>
      )}
      <EditDialog alert={alert} open={editing} onOpenChange={setEditing} checkMinutes={checkMinutes} onSave={(body) => change.mutateAsync(body)} />
      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{alert.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>It stops watching this number. Alerts it already raised are deleted too.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction destructive onClick={() => remove.mutate()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}

export function MyAlerts({ alerts, checkMinutes, onCreate }: { alerts: Alert[]; checkMinutes: number[]; onCreate: () => void }) {
  if (alerts.length === 0) {
    return (
      <EmptyState
        icon={BellPlus}
        title="No alerts yet"
        description="Pick a number to watch, like orders per day. InsightX learns its normal range and tells you when it moves."
        action={
          <Button onClick={onCreate}>
            <BellPlus /> New alert
          </Button>
        }
        className="rounded-xl border border-dashed py-14"
      />
    );
  }
  return (
    <ul className="space-y-3">
      {alerts.map((alert) => (
        <AlertRow key={alert.id} alert={alert} checkMinutes={checkMinutes} />
      ))}
    </ul>
  );
}
