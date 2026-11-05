"use client";

// Public landing page for people who are not signed in. One large 3D data
// crystal follows a list of camera shots (shots.ts) as the page scrolls:
// centred under the headline, beside the text, close up in a corner, inside
// a thin measuring ring. The page itself turns from near-black to a grainy
// silver and back. Sections come into focus as they scroll in. Every claim
// here is a measured one (see evals/README.md).

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Compass, LayoutPanelTop, LineChart, MessageSquareText, ShieldCheck, Target } from "lucide-react";
import { LogoMark } from "@/components/common/logo";
import { CrystalBackdrop } from "@/components/landing/crystal-backdrop";
import { SHOTS, sampleShot } from "@/components/landing/shots";
import { cn } from "@/lib/utils";
import styles from "@/components/landing/landing.module.css";

const FEATURES = [
  {
    id: "ask",
    label: "Ask",
    icon: MessageSquareText,
    title: "Ask in plain English",
    text: "A 13-stage LangGraph agent rewrites the question, finds the right tables and writes several SQL candidates. You see the SQL, the rows and a chart.",
    stat: "PostgreSQL · MySQL · SQL Server · SQLite · CSV",
  },
  {
    id: "safe",
    label: "Safe",
    icon: ShieldCheck,
    title: "Safe by design",
    text: "Every query is parsed with SQLGlot and only a single SELECT is allowed. It runs in a read-only transaction with a timeout and a row limit.",
    stat: "6 / 6 destructive requests blocked in evaluation",
  },
  {
    id: "why",
    label: "Why",
    icon: Compass,
    title: "Find out why",
    text: "Ask why a number changed. InsightX breaks it down by region, product and time, and every number in the explanation is checked against the data.",
    stat: "Numbers verified against the result rows",
  },
  {
    id: "whatif",
    label: "What-if",
    icon: LineChart,
    title: "Test a change first",
    text: "What if prices rise 10%, or a segment disappears? See the impact by segment, a sensitivity curve and a forecast with a 95% band.",
    stat: "Holt-Winters forecast with a backtest",
  },
  {
    id: "measured",
    label: "Measured",
    icon: Target,
    title: "Measured, not guessed",
    text: "Golden-set evaluations, per-question tracing, cost tracking and SLO monitoring show exactly where the system works and where it breaks.",
    stat: "79.8% execution accuracy on a 104-question Spider 1.0 dev subset, free-tier models",
  },
] as const;

const INDEX = [...FEATURES.map(({ id, label }) => ({ id, label })), { id: "inside", label: "Inside" }];
const STEPS = SHOTS.length - 1;

type Rgb = [number, number, number];
const DARK_BG: Rgb = [6, 7, 10];
const LIGHT_BG: Rgb = [198, 200, 207];
const DARK_FG: Rgb = [255, 255, 255];
const LIGHT_FG: Rgb = [14, 15, 19];

function mixRgb(a: Rgb, b: Rgb, t: number): Rgb {
  return [0, 1, 2].map((i) => Math.round(a[i]! + (b[i]! - a[i]!) * t)) as Rgb;
}
const rgb = ([r, g, b]: Rgb, alpha = 1) => `rgb(${r} ${g} ${b} / ${alpha})`;

/** Writes the page colours for a tone between 0 (dark) and 1 (silver). */
function applyTone(root: HTMLElement, tone: number) {
  const bg = mixRgb(DARK_BG, LIGHT_BG, tone);
  const fg = mixRgb(DARK_FG, LIGHT_FG, tone);
  const set = (name: string, value: string) => root.style.setProperty(name, value);
  set("--bg", rgb(bg));
  set("--fg", rgb(fg));
  set("--muted", rgb(fg, 0.66));
  set("--faint", rgb(fg, 0.4));
  set("--line", rgb(fg, 0.16 + 0.06 * tone));
  set("--panel", tone > 0.5 ? rgb([255, 255, 255], 0.38 * tone) : rgb([255, 255, 255], 0.03));
  set("--grid-line", rgb(fg, 0.045 + 0.02 * tone));
  set("--cross", rgb(fg, 0.55));
  set("--light", String(tone));
}

function useReveal() {
  React.useEffect(() => {
    const items = Array.from(document.querySelectorAll<HTMLElement>(`.${styles.reveal}`));
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => entry.target.setAttribute("data-visible", String(entry.isIntersecting))),
      { threshold: 0.2 },
    );
    items.forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, []);
}

function useActiveSection(): string {
  const [active, setActive] = React.useState("intro");
  React.useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => entry.isIntersecting && setActive(entry.target.id)),
      { threshold: 0.5 },
    );
    SHOTS.forEach(({ id }) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, []);
  return active;
}

function Corners({ className, size = "size-2" }: { className?: string; size?: string }) {
  return (
    <>
      {(["-left-3 -top-3", "-right-3 -top-3", "-left-3 -bottom-3", "-right-3 -bottom-3"] as const).map((pos) => (
        <span key={pos} aria-hidden className={cn(styles.cross, "absolute opacity-60", size, pos, className)} />
      ))}
    </>
  );
}

function CtaButton({ href, children, primary = false }: { href: string; children: React.ReactNode; primary?: boolean }) {
  return (
    <Link
      href={href}
      className={cn(
        "group relative inline-flex h-12 items-center gap-2 rounded-full px-7 text-[13px] font-semibold uppercase tracking-[0.14em] transition-opacity",
        primary ? "bg-[var(--fg)] text-[var(--bg)] hover:opacity-85" : "border border-[var(--line)] text-[var(--fg)] hover:opacity-75",
      )}
    >
      {children}
      <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
      <Corners />
    </Link>
  );
}

/* ---------- "Inside the app" panels: small drawings of real screens ---------- */

function Panel({ title, icon: Icon, children, delay }: { title: string; icon: React.ElementType; children: React.ReactNode; delay: string }) {
  return (
    <div
      className={cn(styles.reveal, "relative min-w-0 rounded-sm border border-[var(--line)] bg-[var(--panel)] p-5 backdrop-blur-md")}
      style={{ "--delay": delay } as React.CSSProperties}
    >
      <Corners size="size-3" className="opacity-70" />
      <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--faint)]">
        <Icon className="size-3.5" />
        {title}
      </p>
      <div className="mt-4">{children}</div>
    </div>
  );
}

function AskPanel() {
  const bars = [38, 52, 47, 63, 71, 84];
  return (
    <>
      <p className="rounded-md border border-[var(--line)] px-3 py-2 text-sm text-[var(--fg)]">Revenue by month for the last 6 months</p>
      <pre className="mt-3 overflow-x-auto rounded-md bg-black/80 p-3 font-mono text-[11px] leading-relaxed text-violet-200">
        {"SELECT date_trunc('month', order_date) AS month,\n       SUM(amount) AS revenue\nFROM orders\nGROUP BY 1 ORDER BY 1 LIMIT 6;"}
      </pre>
      <svg viewBox="0 0 240 90" className="mt-3 w-full" aria-hidden>
        {bars.map((h, i) => (
          <rect key={i} x={8 + i * 38} y={86 - h} width="24" height={h} rx="2" fill="currentColor" className="text-violet-500" opacity={0.45 + i * 0.1} />
        ))}
      </svg>
      <p className="mt-2 text-xs text-[var(--muted)]">Read-only SELECT · rows and chart shown together</p>
    </>
  );
}

function WhyPanel() {
  const drivers = [
    { label: "Region", value: 72 },
    { label: "Product category", value: 48 },
    { label: "Sales channel", value: 21 },
  ];
  return (
    <>
      <p className="rounded-md border border-[var(--line)] px-3 py-2 text-sm text-[var(--fg)]">Why did revenue change last month?</p>
      <ul className="mt-4 space-y-3">
        {drivers.map((driver) => (
          <li key={driver.label}>
            <div className="flex justify-between text-xs text-[var(--muted)]">
              <span>{driver.label}</span>
              <span className="font-mono">share of change</span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-[var(--line)]">
              <div className="h-full rounded-full bg-violet-500" style={{ width: `${driver.value}%` }} />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-[var(--muted)]">Drivers ranked · every number checked against the rows</p>
    </>
  );
}

function WhatIfPanel() {
  const history = "M8 70 L40 62 L72 66 L104 52 L136 48";
  const forecast = "M136 48 L168 40 L200 34 L232 27";
  const band = "M136 48 L168 30 L200 20 L232 10 L232 44 L200 48 L168 50 L136 48 Z";
  return (
    <>
      <div className="flex items-center gap-3 rounded-md border border-[var(--line)] px-3 py-2 text-sm text-[var(--fg)]">
        <span>Price</span>
        <span className="relative h-1 flex-1 rounded-full bg-[var(--line)]">
          <span className="absolute inset-y-0 left-0 w-3/5 rounded-full bg-violet-500" />
          <span className="absolute left-3/5 top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-violet-500 bg-[var(--bg)]" />
        </span>
        <span className="font-mono text-xs">+10%</span>
      </div>
      <svg viewBox="0 0 240 90" className="mt-4 w-full text-violet-500" aria-hidden>
        <path d={band} fill="currentColor" opacity="0.15" />
        <path d={history} fill="none" stroke="currentColor" strokeWidth="2" />
        <path d={forecast} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="4 4" />
        <line x1="136" y1="4" x2="136" y2="86" stroke="currentColor" strokeOpacity="0.3" />
      </svg>
      <p className="mt-2 text-xs text-[var(--muted)]">Impact by segment · forecast with a 95% band</p>
    </>
  );
}

/* ---------------------------------- page ---------------------------------- */

export function LandingPage() {
  const root = React.useRef<HTMLDivElement>(null);
  const fill = React.useRef<HTMLDivElement>(null);
  const progress = React.useRef(0);
  const active = useActiveSection();
  useReveal();

  React.useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      // Position in "sections": 2.5 = halfway from the third section to the fourth.
      const y = window.scrollY;
      const tops = SHOTS.map(({ id }) => document.getElementById(id)?.offsetTop ?? 0);
      const max = document.documentElement.scrollHeight - window.innerHeight;
      tops[tops.length - 1] = Math.min(tops[tops.length - 1] ?? max, max);
      let u = 0;
      for (let i = 0; i < STEPS; i++) {
        const a = tops[i] ?? 0;
        const b = tops[i + 1] ?? a;
        if (y >= b) u = i + 1;
        else if (y >= a) u = i + (b > a ? (y - a) / (b - a) : 0);
      }
      progress.current = u / STEPS;
      if (root.current) applyTone(root.current, sampleShot(u).tone);
      if (fill.current) {
        // Fills across the feature and "inside" sections only.
        const k = Math.min(1, Math.max(0, (u - 1) / (INDEX.length - 1)));
        fill.current.style.transform = `scaleY(${k})`;
      }
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return (
    <div ref={root} className="dark relative min-h-dvh overflow-x-clip bg-[var(--bg,#06070a)] text-[var(--fg,#fff)]">
      {/* fixed stage: silver light, grid, 3D crystal, grain, frame */}
      <div aria-hidden className="pointer-events-none fixed inset-0">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_70%_60%_at_45%_40%,rgb(255_255_255/0.75),transparent_70%)]" style={{ opacity: "var(--light, 0)" }} />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_55%,rgb(124_58_237/0.18),transparent_70%)]" style={{ opacity: "calc(1 - var(--light, 0))" }} />
        <div className={cn(styles.grid, "absolute inset-0")} />
        <CrystalBackdrop progress={progress} className="absolute inset-0 max-md:translate-y-[26vh] max-md:opacity-70" />
        <div className={cn(styles.grain, "absolute inset-0 opacity-[0.22]")} />
        <div className="absolute inset-4 rounded-sm border border-[var(--line)] opacity-50 sm:inset-6" />
        {(["left-3 top-3 sm:left-5 sm:top-5", "right-3 top-3 sm:right-5 sm:top-5", "left-3 bottom-3 sm:left-5 sm:bottom-5", "right-3 bottom-3 sm:right-5 sm:bottom-5"] as const).map((pos) => (
          <span key={pos} className={cn(styles.cross, "absolute size-2.5", pos)} />
        ))}
      </div>

      <header className="fixed inset-x-0 top-0 z-20 flex h-20 items-center justify-between bg-gradient-to-b from-[var(--bg,#06070a)] to-transparent px-6 sm:px-12">
        <Link href="/" className="flex items-center gap-2.5" aria-label="InsightX home">
          <LogoMark />
          <span className="text-[15px] font-semibold tracking-tight">InsightX</span>
        </Link>
        <nav className="flex items-center gap-3">
          <Link href="/sign-in" className="hidden h-10 items-center rounded-full px-5 text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)] hover:text-[var(--fg)] sm:inline-flex">
            Sign in
          </Link>
          <Link href="/sign-in" className="inline-flex h-10 items-center rounded-full border border-[var(--line)] px-5 text-[12px] font-semibold uppercase tracking-[0.14em] hover:opacity-75">
            Get started
          </Link>
        </nav>
      </header>

      {/* section index with a progress line that fills as you scroll */}
      <nav aria-label="Sections" className="fixed left-10 top-1/2 z-20 hidden -translate-y-1/2 xl:block">
        <div className="relative rounded-sm bg-[var(--panel)] py-1 pl-5 pr-4 backdrop-blur-md">
          <span aria-hidden className="absolute inset-y-0 left-0 w-px bg-[var(--line)]" />
          <div ref={fill} aria-hidden className="absolute inset-y-0 left-0 w-px origin-top bg-[var(--fg)]" style={{ transform: "scaleY(0)" }} />
          <ol className="space-y-6">
            {INDEX.map((item, index) => (
              <li key={item.id}>
                <a href={`#${item.id}`} className={cn("block transition-colors", active === item.id ? "text-[var(--fg)]" : "text-[var(--faint)] hover:text-[var(--muted)]")}>
                  <span className="block font-mono text-[11px]">{String(index + 1).padStart(2, "0")}</span>
                  <span className="block text-[12px] font-semibold uppercase tracking-[0.14em]">{item.label}</span>
                </a>
              </li>
            ))}
          </ol>
        </div>
      </nav>

      <main className="relative z-10">
        <section id="intro" className="flex min-h-dvh flex-col items-center justify-start px-6 pt-[18vh] text-center">
          <p className={cn(styles.reveal, "text-[12px] font-semibold uppercase tracking-[0.3em] text-[var(--faint)]")}>AI data analyst</p>
          <h1 className="mt-5 max-w-4xl text-[clamp(2.6rem,7vw,5.6rem)] font-semibold uppercase leading-[0.95] tracking-tight">
            {["Ask your data", "anything"].map((line, i) => (
              <span key={line} className={cn(styles.reveal, "block")} style={{ "--delay": `${120 + i * 120}ms` } as React.CSSProperties}>
                {line}
              </span>
            ))}
          </h1>
          <p className={cn(styles.reveal, "mt-6 max-w-xl text-base text-[var(--muted)] sm:text-lg")} style={{ "--delay": "360ms" } as React.CSSProperties}>
            Plain-English questions become safe, read-only SQL. Every number in the answer is checked against the real rows, and InsightX explains why a metric changed.
          </p>
          <div className={cn(styles.reveal, "mt-10 flex flex-wrap items-center justify-center gap-5")} style={{ "--delay": "480ms" } as React.CSSProperties}>
            <CtaButton href="/sign-in" primary>
              Get started
            </CtaButton>
            <CtaButton href="#ask">See how it works</CtaButton>
          </div>
        </section>

        {FEATURES.map((feature, index) => {
          const left = index % 2 === 0;
          return (
            <section key={feature.id} id={feature.id} className="flex min-h-dvh items-center px-8 sm:px-16 lg:px-36 xl:px-48">
              <div className={cn("w-full max-w-xl", left ? "mr-auto" : "ml-auto")}>
                <p className={cn(styles.reveal, "flex items-center gap-3 font-mono text-[12px] text-[var(--faint)]")}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <span className="h-px w-10 bg-[var(--line)]" />
                  <feature.icon className="size-4 text-violet-500" />
                </p>
                <h2 className={cn(styles.reveal, "mt-5 text-[clamp(2rem,4.6vw,3.6rem)] font-semibold uppercase leading-[1] tracking-tight")} style={{ "--delay": "100ms" } as React.CSSProperties}>
                  {feature.title}
                </h2>
                <p className={cn(styles.reveal, "mt-6 text-base leading-relaxed text-[var(--muted)] sm:text-lg")} style={{ "--delay": "200ms" } as React.CSSProperties}>
                  {feature.text}
                </p>
                <p className={cn(styles.reveal, "relative mt-8 inline-flex rounded-full border border-[var(--line)] bg-[var(--panel)] px-4 py-2 text-[13px] text-[var(--fg)] backdrop-blur")} style={{ "--delay": "300ms" } as React.CSSProperties}>
                  {feature.stat}
                </p>
              </div>
            </section>
          );
        })}

        <section id="inside" className="flex min-h-dvh flex-col justify-center px-8 py-28 sm:px-16 lg:px-36 xl:px-48">
          <p className={cn(styles.reveal, "flex items-center gap-3 font-mono text-[12px] text-[var(--faint)]")}>
            <span>06</span>
            <span className="h-px w-10 bg-[var(--line)]" />
            <LayoutPanelTop className="size-4 text-violet-500" />
          </p>
          <h2 className={cn(styles.reveal, "mt-5 max-w-3xl text-[clamp(2rem,4.6vw,3.6rem)] font-semibold uppercase leading-[1] tracking-tight")} style={{ "--delay": "100ms" } as React.CSSProperties}>
            Inside the app
          </h2>
          <p className={cn(styles.reveal, "mt-4 max-w-xl text-[var(--muted)]")} style={{ "--delay": "160ms" } as React.CSSProperties}>
            Simplified drawings of three screens: asking a question, finding out why, and testing a change.
          </p>
          <div className="mt-12 grid grid-cols-1 gap-8 md:grid-cols-3">
            <Panel title="Ask" icon={MessageSquareText} delay="0ms">
              <AskPanel />
            </Panel>
            <Panel title="Why" icon={Compass} delay="140ms">
              <WhyPanel />
            </Panel>
            <Panel title="What-if" icon={LineChart} delay="280ms">
              <WhatIfPanel />
            </Panel>
          </div>
        </section>

        <section id="start" className="flex min-h-dvh flex-col items-center justify-start px-6 pt-[18vh] text-center">
          <h2 className={cn(styles.reveal, "max-w-3xl text-[clamp(2.4rem,6vw,4.8rem)] font-semibold uppercase leading-[0.95] tracking-tight")}>
            Ask your first question
          </h2>
          <p className={cn(styles.reveal, "mt-6 max-w-lg text-[var(--muted)]")} style={{ "--delay": "120ms" } as React.CSSProperties}>
            Connect a database or upload a CSV. Your database password stays on your server.
          </p>
          <div className={cn(styles.reveal, "mt-10 flex flex-wrap items-center justify-center gap-5")} style={{ "--delay": "240ms" } as React.CSSProperties}>
            <CtaButton href="/sign-in" primary>
              Get started
            </CtaButton>
            <CtaButton href="/sign-in">Sign in</CtaButton>
          </div>
          <p className="mt-16 text-[11px] uppercase tracking-[0.2em] text-[var(--faint)]">InsightX · built on free-tier LLMs</p>
        </section>
      </main>
    </div>
  );
}
