"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { RefreshCw, ServerCrash } from "lucide-react";
import { LogoMark } from "@/components/common/logo";
import { GridPattern } from "@/components/fx/grid-pattern";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useOpenAlertCount } from "@/components/layout/alerts-bell";
import { CommandMenu } from "@/components/layout/command-menu";
import { SidebarContent } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { useAuth } from "@/components/providers/auth-provider";
import { readPref, writePref } from "@/lib/ui-prefs";
import { cn } from "@/lib/utils";

function Splash() {
  return (
    <div className="flex min-h-dvh items-center justify-center" role="status" aria-label="Loading InsightX">
      <div className="relative">
        <div aria-hidden className="absolute inset-0 -z-10 scale-[2.2] rounded-full bg-[var(--glow)] blur-2xl" />
        <LogoMark className="size-10 animate-pulse" />
      </div>
    </div>
  );
}

function Offline({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex min-h-dvh items-center justify-center p-6">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 flex size-11 items-center justify-center rounded-xl border bg-card">
          <ServerCrash className="size-5 text-critical" />
        </div>
        <h1 className="text-lg font-semibold">Backend not reachable</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">{message}</p>
        <p className="mt-3 text-xs text-muted-foreground">
          Start it with <code className="rounded bg-muted px-1 py-0.5 font-mono">uvicorn backend.main:app --port 8000</code>
        </p>
        <Button className="mt-5" onClick={onRetry}>
          <RefreshCw /> Try again
        </Button>
      </div>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [searchOpen, setSearchOpen] = React.useState(false);
  const openAlerts = useOpenAlertCount();

  React.useEffect(() => setCollapsed(readPref("sidebarCollapsed", false)), []);
  React.useEffect(() => setMobileOpen(false), [pathname]);

  const toggle = React.useCallback(() => {
    setCollapsed((value) => {
      writePref("sidebarCollapsed", !value);
      return !value;
    });
  }, []);

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = !!target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((open) => !open);
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggle();
      } else if (!typing && !event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        router.push("/ask");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, toggle]);

  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground">
        Skip to content
      </a>
      <aside
        className={cn(
          "sticky top-0 hidden h-dvh shrink-0 border-r border-sidebar-border bg-sidebar transition-[width] duration-200 ease-out lg:block",
          collapsed ? "w-[60px]" : "w-64",
        )}
        aria-label="Main navigation"
      >
        <SidebarContent collapsed={collapsed} onToggle={toggle} openAlerts={openAlerts} />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" aria-describedby={undefined}>
          <SheetTitle className="sr-only">Menu</SheetTitle>
          <SidebarContent onNavigate={() => setMobileOpen(false)} openAlerts={openAlerts} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onOpenMenu={() => setMobileOpen(true)} onOpenSearch={() => setSearchOpen(true)} />
        <main id="main" className="relative isolate flex min-w-0 flex-1 flex-col">
          <GridPattern variant="dots" className="h-[420px]" />
          <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[360px] bg-[radial-gradient(ellipse_60%_100%_at_50%_-30%,var(--glow),transparent_70%)] opacity-70" />
          <React.Suspense fallback={null}>
            <div key={pathname} className="fx-enter flex min-w-0 flex-1 flex-col">
              {children}
            </div>
          </React.Suspense>
        </main>
      </div>
      <CommandMenu open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { state, retry } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  React.useEffect(() => {
    if (state.phase === "signed-out") router.replace(`/sign-in?next=${encodeURIComponent(pathname)}`);
  }, [state.phase, router, pathname]);

  if (state.phase === "checking" || state.phase === "signed-out") return <Splash />;
  if (state.phase === "offline") return <Offline message={state.message} onRetry={retry} />;
  return <Shell>{children}</Shell>;
}
