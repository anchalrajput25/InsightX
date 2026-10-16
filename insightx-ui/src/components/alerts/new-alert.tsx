"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BellPlus, CheckCircle2, ListChecks, Loader2, Play, Send, Sparkles, X } from "lucide-react";
import { AlertChart } from "@/components/alerts/alert-chart";
import { DEFAULT_RULE, FREQUENCY } from "@/components/alerts/alert-utils";
import { cleanRule, RuleEditor } from "@/components/alerts/rule-editor";
import { Verdict } from "@/components/alerts/verdict";
import { CodeBlock } from "@/components/common/code-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { alertsApi } from "@/lib/api/alerts";
import { errorMessage } from "@/lib/api/client";
import type { AlertPreview, AlertRule, MetricSuggestion } from "@/lib/types";
import { cn } from "@/lib/utils";

type Source = "suggested" | "words";

function Step({ n, title, children, done }: { n: number; title: string; children: React.ReactNode; done?: boolean }) {
  return (
    <section className="relative pl-9">
      <span
        className={cn(
          "tabular absolute left-0 top-0 flex size-6 items-center justify-center rounded-full border text-[11px] font-semibold",
          done ? "border-success/40 bg-success/10 text-success" : "text-muted-foreground",
        )}
      >
        {done ? <CheckCircle2 className="size-3.5" /> : n}
      </span>
      <h3 className="mb-3 text-sm font-medium leading-6">{title}</h3>
      {children}
    </section>
  );
}

export function NewAlertSheet({ open, onOpenChange, connectionId, emailReady, checkMinutes }: { open: boolean; onOpenChange: (open: boolean) => void; connectionId: string; emailReady: boolean; checkMinutes: number[] }) {
  const queryClient = useQueryClient();
  const [source, setSource] = React.useState<Source>("suggested");
  const [grain, setGrain] = React.useState<"day" | "week" | "month">("day");
  const [picked, setPicked] = React.useState<MetricSuggestion | null>(null);
  const [words, setWords] = React.useState("");
  const [rule, setRule] = React.useState<AlertRule>(DEFAULT_RULE);
  const [preview, setPreview] = React.useState<AlertPreview | null>(null);
  const [previewing, setPreviewing] = React.useState(false);
  const [previewError, setPreviewError] = React.useState("");
  const [name, setName] = React.useState("");
  const [minutes, setMinutes] = React.useState(60);
  const [webhook, setWebhook] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);

  const suggestions = useQuery({
    queryKey: ["alerts", "suggestions", connectionId, grain],
    queryFn: () => alertsApi.suggestions(connectionId, grain),
    enabled: open && source === "suggested",
    staleTime: 5 * 60_000,
  });

  React.useEffect(() => {
    if (!open) {
      setPicked(null);
      setWords("");
      setRule(DEFAULT_RULE);
      setPreview(null);
      setPreviewError("");
      setName("");
      setWebhook("");
      setEmail("");
    }
  }, [open]);

  // A different number means the old preview no longer applies.
  React.useEffect(() => {
    setPreview(null);
    setPreviewError("");
  }, [source, picked, words]);

  const metric = () => {
    if (preview) return { sql: preview.sql, name: name.trim() || preview.name, fill_gaps: preview.fill_gaps };
    if (source === "suggested" && picked) return { sql: picked.sql, name: name.trim() || picked.name, fill_gaps: picked.fill_gaps };
    return { metric_text: words.trim(), name: name.trim() || undefined, fill_gaps: false };
  };
  const ready = source === "suggested" ? !!picked : words.trim().length > 2;
  const grainNow = preview?.grain ?? (source === "suggested" ? (picked?.grain ?? grain) : null);
  const single = preview?.single ?? false;

  const runPreview = async () => {
    setPreviewing(true);
    setPreviewError("");
    try {
      const result = await alertsApi.preview({ connection_id: connectionId, rule: cleanRule(rule), ...metric() });
      setPreview(result);
      if (!name.trim()) setName(result.name);
    } catch (error) {
      setPreviewError(errorMessage(error));
    } finally {
      setPreviewing(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const result = await alertsApi.create({
        connection_id: connectionId,
        rule: cleanRule(rule),
        check_minutes: minutes,
        webhook: webhook.trim() || undefined,
        email: email.trim() || undefined,
        ...metric(),
        ...(source === "words" ? { metric_text: words.trim() } : {}),
      });
      toast.success(`Alert "${result.alert.name}" saved`, {
        description: result.first_check.fired ? "It is already outside the normal range." : "First check done. It is watching now.",
      });
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      onOpenChange(false);
    } catch (error) {
      toast.error("Could not save the alert", { description: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  };

  const testChannels = async () => {
    setTesting(true);
    try {
      const { results } = await alertsApi.testNotification(webhook.trim(), email.trim());
      toast.message("Test sent", { description: results.join(" · ") });
    } catch (error) {
      toast.error("Test failed", { description: errorMessage(error) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full max-w-none bg-background sm:max-w-2xl" aria-describedby={undefined}>
        <div className="flex h-14 shrink-0 items-center justify-between border-b px-5">
          <SheetTitle className="flex items-center gap-2 text-[15px] font-semibold">
            <BellPlus className="size-4 text-primary" /> New alert
          </SheetTitle>
          <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => onOpenChange(false)}>
            <X />
          </Button>
        </div>

        <div className="scrollbar-thin flex-1 space-y-8 overflow-y-auto px-5 py-6">
          <Step n={1} title="What number should I watch?" done={ready}>
            <div className="mb-4 flex gap-1 rounded-lg bg-muted/60 p-0.5" role="radiogroup" aria-label="How to choose the number">
              {(
                [
                  ["suggested", "Pick a suggested number", ListChecks],
                  ["words", "Describe it in words", Sparkles],
                ] as const
              ).map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={source === value}
                  onClick={() => setSource(value)}
                  className={cn(
                    "inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
                    source === value && "bg-card text-foreground shadow-sm",
                  )}
                >
                  <Icon className="size-3.5" /> {label}
                </button>
              ))}
            </div>

            {source === "suggested" ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  Per
                  {(["day", "week", "month"] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={grain === value}
                      onClick={() => {
                        setGrain(value);
                        setPicked(null);
                      }}
                      className={cn("rounded-md border px-2 py-0.5 capitalize hover:text-foreground", grain === value && "border-primary/50 bg-primary/10 text-foreground")}
                    >
                      {value}
                    </button>
                  ))}
                </div>
                {suggestions.isPending ? (
                  <div className="space-y-2">
                    <Skeleton className="h-12" />
                    <Skeleton className="h-12" />
                    <Skeleton className="h-12" />
                  </div>
                ) : suggestions.isError ? (
                  <p className="text-sm text-critical">{errorMessage(suggestions.error)}</p>
                ) : (suggestions.data?.suggestions ?? []).length === 0 ? (
                  <p className="rounded-lg border border-dashed px-4 py-5 text-center text-sm text-muted-foreground">No dated tables found to suggest from. Describe the number in words instead.</p>
                ) : (
                  <ul role="radiogroup" aria-label="Suggested numbers" className="scrollbar-thin max-h-72 space-y-1.5 overflow-y-auto pr-1">
                    {(suggestions.data?.suggestions ?? []).map((item) => (
                      <li key={item.key}>
                        <button
                          type="button"
                          role="radio"
                          aria-checked={picked?.key === item.key}
                          onClick={() => setPicked(item)}
                          className={cn(
                            "w-full rounded-lg border px-3 py-2.5 text-left transition-colors hover:border-foreground/20",
                            picked?.key === item.key && "border-primary/60 bg-primary/[0.06]",
                          )}
                        >
                          <span className="block text-[13px] font-medium">{item.name}</span>
                          <span className="block text-xs text-muted-foreground">{item.description}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="alert-words">The number, in your words</Label>
                <Textarea id="alert-words" rows={2} value={words} onChange={(event) => setWords(event.target.value)} placeholder="Number of orders per day" maxLength={1000} />
                <p className="text-xs text-muted-foreground">The AI turns this into SQL once, when you preview or save.</p>
              </div>
            )}
          </Step>

          <Step n={2} title="When should I warn you?">
            <RuleEditor rule={rule} onChange={setRule} grain={grainNow} single={single} idPrefix="new-rule" />
          </Step>

          <Step n={3} title="Try it on your data" done={!!preview}>
            <div className="space-y-4">
              <Button variant="outline" size="sm" onClick={runPreview} disabled={!ready || previewing}>
                {previewing ? <Loader2 className="animate-spin" /> : <Play />}
                {previewing ? "Checking past data…" : preview ? "Preview again" : "Preview"}
              </Button>
              {previewError && <p role="alert" className="text-sm text-critical">{previewError}</p>}
              {preview && (
                <div className="space-y-4">
                  <Verdict evaluation={preview.evaluation} />
                  <div className="rounded-xl border bg-card p-4">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-[13px] font-medium">On the past data</p>
                      <Badge variant={preview.backtest.fired_count ? "warning" : "success"}>
                        Would have alerted {preview.backtest.fired_count} time{preview.backtest.fired_count === 1 ? "" : "s"} in {preview.backtest.checked} checks
                      </Badge>
                    </div>
                    <AlertChart backtest={preview.backtest} rule={preview.rule} />
                  </div>
                  <details className="group">
                    <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">SQL used{preview.used_ai ? " (written by the AI)" : ""}</summary>
                    <CodeBlock code={preview.sql} className="mt-2" />
                  </details>
                </div>
              )}
            </div>
          </Step>

          <Step n={4} title="Name, schedule and where to tell you">
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="alert-name">Name</Label>
                  <Input id="alert-name" value={name} onChange={(event) => setName(event.target.value)} placeholder={picked?.name ?? "My number"} maxLength={300} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="alert-every">Check</Label>
                  <Select value={String(minutes)} onValueChange={(value) => setMinutes(Number(value))}>
                    <SelectTrigger id="alert-every">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(checkMinutes.length ? checkMinutes : Object.keys(FREQUENCY).map(Number)).map((value) => (
                        <SelectItem key={value} value={String(value)}>
                          {FREQUENCY[value] ?? `Every ${value} minutes`}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="alert-webhook">Web hook (Slack, Teams, Discord)</Label>
                  <Input id="alert-webhook" type="url" value={webhook} onChange={(event) => setWebhook(event.target.value)} placeholder="https://hooks.slack.com/…" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="alert-email">E-mail</Label>
                  <Input id="alert-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" disabled={!emailReady} />
                  {!emailReady && <p className="text-xs text-muted-foreground">E-mail needs the INSIGHTX_SMTP settings on the server.</p>}
                </div>
              </div>
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">Alerts always show here and on the bell, even without these.</p>
                <Button variant="ghost" size="sm" onClick={testChannels} disabled={testing || (!webhook.trim() && !email.trim())}>
                  {testing ? <Loader2 className="animate-spin" /> : <Send />} Send a test
                </Button>
              </div>
            </div>
          </Step>
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2 border-t px-5 py-3">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!ready || saving}>
            {saving ? <Loader2 className="animate-spin" /> : <BellPlus />}
            {saving ? "Saving…" : "Save alert"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
