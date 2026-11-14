"use client";

import * as React from "react";
import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Compass,
  Database,
  Eye,
  Hash,
  LineChart,
  MessageSquareText,
  Mic,
  Rows3,
  Sparkles,
  Square,
  type LucideIcon,
} from "lucide-react";
import { NAV_MAIN } from "@/components/layout/nav";
import { useVoiceErrorToast } from "@/components/voice/voice-buttons";
import { useAuth } from "@/components/providers/auth-provider";
import { useChats } from "@/hooks/use-chats";
import { useSpeechInput } from "@/lib/speech";
import { cn } from "@/lib/utils";

type Kind = "answer" | "investigation";
type Idea = { text: string; icon: LucideIcon; kind?: Kind };
type Group = { id: string; label: string; icon: LucideIcon; hint: string; ideas: Idea[] };

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function firstName(name: string): string {
  const first = name.trim().split(/[\s@._]+/)[0] ?? "";
  if (!first || first.toLowerCase() === "you") return "";
  return first.charAt(0).toUpperCase() + first.slice(1);
}

function buildGroups(tables: string[], dated: string[]): Group[] {
  const pick = (list: string[], index: number) => list[index % Math.max(list.length, 1)];
  const unique = (ideas: Idea[]) => ideas.filter((idea, i) => ideas.findIndex((other) => other.text === idea.text) === i);
  const groups: Group[] = [];
  if (tables.length) {
    groups.push({
      id: "quick",
      label: "Quick look",
      icon: Eye,
      hint: "Simple counts and samples to get started",
      ideas: unique([
        { text: "Which tables have the most data?", icon: Database },
        { text: `How many rows does ${pick(tables, 0)} have?`, icon: Hash },
        { text: `Show 10 sample rows from ${pick(tables, 1)}`, icon: Rows3 },
        { text: `Show 10 sample rows from ${pick(tables, 2)}`, icon: Rows3 },
      ]),
    });
  }
  if (dated.length) {
    groups.push({
      id: "trends",
      label: "Trends and charts",
      icon: LineChart,
      hint: "See how things change over time, with a chart",
      ideas: unique([
        { text: `How many rows does ${pick(dated, 0)} have per month?`, icon: LineChart },
        { text: `Which month had the most ${pick(dated, 0)} records?`, icon: BarChart3 },
        { text: `How many rows does ${pick(dated, 1)} have per month?`, icon: LineChart },
        { text: `Show the daily count of ${pick(dated, 1)} for the last 30 days`, icon: BarChart3 },
      ]),
    });
    groups.push({
      id: "why",
      label: "Find out why",
      icon: Compass,
      hint: "InsightX compares periods and tests the causes for you",
      ideas: unique([
        { text: `Why did ${pick(dated, 0)} change last month?`, icon: Compass, kind: "investigation" },
        { text: `Why did ${pick(dated, 1)} change last month?`, icon: Compass, kind: "investigation" },
        { text: `Why did ${pick(dated, 2)} change last month?`, icon: Compass, kind: "investigation" },
      ]),
    });
  }
  return groups;
}

function VoiceOrb({ onText, busy }: { onText: (text: string) => void; busy?: boolean }) {
  const [heard, setHeard] = React.useState(false);
  const voice = useSpeechInput({
    onFinal: (text) => {
      setHeard(true);
      onText(text);
    },
  });
  useVoiceErrorToast(voice.error);
  const caption = voice.listening
    ? voice.interim || "Listening… ask your question"
    : !voice.supported
      ? "Voice works in Chrome or Edge"
      : heard
        ? "Your question is in the box below. Edit it if needed, then press send."
        : "Tap the mic and ask out loud";

  return (
    <div className="fx-enter flex flex-col items-center" style={{ ["--d" as string]: "180ms" }}>
      <button
        type="button"
        onClick={voice.toggle}
        disabled={busy && !voice.listening}
        aria-label={voice.listening ? "Stop listening" : "Speak your question"}
        aria-pressed={voice.listening}
        className={cn(
          "group relative flex size-20 cursor-pointer items-center justify-center rounded-full text-primary-foreground shadow-[0_12px_40px_-10px_var(--primary)] transition-transform duration-200 hover:scale-105 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60",
          voice.listening ? "bg-critical" : "bg-[linear-gradient(135deg,var(--primary),color-mix(in_oklch,var(--primary)_60%,var(--info)))]",
        )}
      >
        {voice.listening && (
          <>
            <span aria-hidden className="absolute inset-0 animate-ping rounded-full bg-critical/30" />
            <span aria-hidden className="absolute -inset-3 animate-pulse rounded-full border-2 border-critical/30" />
          </>
        )}
        {!voice.listening && <span aria-hidden className="absolute -inset-2 rounded-full border border-primary/25 transition-all duration-300 group-hover:-inset-3 group-hover:border-primary/40" />}
        {voice.listening ? <Square className="relative size-6 fill-current" /> : <Mic className="relative size-8" />}
      </button>
      <p className={cn("mt-4 min-h-5 max-w-md text-center text-sm", voice.listening ? "font-medium text-foreground" : "text-muted-foreground")} aria-live="polite">
        {caption}
      </p>
    </div>
  );
}

export function HomeHub({
  tables,
  dated,
  connectionLabel,
  busy,
  onAsk,
  onVoiceText,
}: {
  tables: string[];
  dated: string[];
  connectionLabel: string;
  busy?: boolean;
  onAsk: (question: string, kind?: Kind, voice?: boolean) => void;
  /** Spoken words go into the question box, not straight to an answer. */
  onVoiceText: (text: string) => void;
}) {
  const { displayName } = useAuth();
  const name = firstName(displayName);
  const groups = React.useMemo(() => buildGroups(tables, dated), [tables, dated]);
  const [active, setActive] = React.useState(groups[0]?.id ?? "");
  const group = groups.find((item) => item.id === active) ?? groups[0];
  const { data: chats } = useChats();
  const recent = (chats ?? []).filter((chat) => chat.title && chat.title !== "New Chat").slice(0, 4);
  const shortcuts = NAV_MAIN.filter((item) => item.href !== "/ask" && item.href !== "/pipeline");

  return (
    <div className="relative isolate mx-auto w-full max-w-4xl flex-1 px-4 py-8 sm:px-6 sm:py-10">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-72 bg-[radial-gradient(60%_60%_at_50%_0%,var(--glow),transparent)]" />

      {/* Greeting */}
      <div className="flex flex-col items-center text-center">
        <span className="fx-enter inline-flex items-center gap-2 rounded-full border bg-card/80 px-3 py-1 text-xs text-muted-foreground backdrop-blur-sm">
          <span className="size-1.5 rounded-full bg-success" />
          Connected to <span className="font-medium text-foreground">{connectionLabel}</span>
          <span className="text-muted-foreground/70">·</span>
          {tables.length} table{tables.length === 1 ? "" : "s"}
          <Link href="/connect" className="ml-1 font-medium text-primary hover:underline">
            Switch
          </Link>
        </span>
        <h1 className="fx-enter mt-5 text-3xl font-semibold tracking-tight sm:text-4xl" style={{ ["--d" as string]: "60ms" }}>
          <span className="fx-title">
            {greeting()}
            {name ? `, ${name}` : ""}.{" "}
          </span>
          <span className="fx-gradient-text">What shall we find?</span>
        </h1>
        <p className="fx-enter mt-2.5 max-w-lg text-sm text-muted-foreground" style={{ ["--d" as string]: "120ms" }}>
          Speak or type a question in plain English. You get the answer, a chart and the SQL behind it.
        </p>
      </div>

      <div className="mt-8">
        <VoiceOrb busy={busy} onText={onVoiceText} />
      </div>

      {/* Starter questions */}
      {group && (
        <section className="mt-10" aria-label="Starter questions">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Sparkles className="size-4 text-primary" /> Try one of these
            </div>
            <div role="tablist" aria-label="Question type" className="flex flex-wrap gap-1 rounded-full border bg-card p-1">
              {groups.map((item) => {
                const on = item.id === group.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    aria-selected={on}
                    onClick={() => setActive(item.id)}
                    className={cn(
                      "inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
                      on && "bg-primary text-primary-foreground hover:text-primary-foreground",
                    )}
                  >
                    <item.icon className="size-3.5" /> {item.label}
                  </button>
                );
              })}
            </div>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{group.hint}</p>
          <div key={group.id} className="fx-stagger mt-4 grid gap-3 sm:grid-cols-2">
            {group.ideas.map((idea) => (
              <button
                key={idea.text}
                type="button"
                disabled={busy}
                onClick={() => onAsk(idea.text, idea.kind)}
                onPointerMove={(event) => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  event.currentTarget.style.setProperty("--mx", `${event.clientX - rect.left}px`);
                  event.currentTarget.style.setProperty("--my", `${event.clientY - rect.top}px`);
                }}
                className="fx-spotlight fx-surface group flex cursor-pointer items-center gap-3 rounded-2xl border bg-card p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[0_10px_30px_-14px_var(--glow)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-xl", idea.kind === "investigation" ? "bg-warning/15 text-warning" : "bg-primary/10 text-primary")}>
                  <idea.icon className="size-4" />
                </span>
                <span className="flex-1 text-sm leading-snug">{idea.text}</span>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-primary" />
              </button>
            ))}
          </div>
        </section>
      )}

      {/* Shortcuts and recent chats */}
      <div className={cn("mt-10 grid gap-6", recent.length > 0 && "lg:grid-cols-[1.4fr_1fr]")}>
        <section aria-label="Shortcuts">
          <p className="mb-3 text-sm font-medium">Jump to</p>
          <div className="fx-stagger grid grid-cols-2 gap-3 sm:grid-cols-4">
            {shortcuts.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="fx-surface group flex flex-col gap-2 rounded-2xl border bg-card p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40"
              >
                <span className="flex items-center justify-between">
                  <span className="flex size-8 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                    <item.icon className="size-4" />
                  </span>
                  <ArrowUpRight className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </span>
                <span className="text-sm font-medium">{item.label}</span>
                <span className="text-[11px] leading-snug text-muted-foreground">{item.description}</span>
              </Link>
            ))}
          </div>
        </section>
        {recent.length > 0 && (
          <section aria-label="Recent chats">
            <p className="mb-3 text-sm font-medium">Pick up where you left off</p>
            <div className="divide-y overflow-hidden rounded-2xl border bg-card">
              {recent.map((chat) => (
                <Link
                  key={chat.thread_id}
                  href={`/ask?chat=${encodeURIComponent(chat.thread_id)}`}
                  className="group flex items-center gap-3 px-3.5 py-3 text-sm transition-colors hover:bg-accent/60"
                >
                  <MessageSquareText className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
                  <span className="flex-1 truncate">{chat.title}</span>
                  <ArrowRight className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
