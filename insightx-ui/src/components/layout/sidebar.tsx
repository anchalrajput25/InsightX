"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Database, PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import { Logo } from "@/components/common/logo";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/ui/tooltip";
import { ChatList } from "@/components/layout/chat-list";
import { isActive, NAV_LLMOPS, NAV_MAIN, NAV_MONITOR, NAV_SETTINGS, type NavItem } from "@/components/layout/nav";
import { useConnection } from "@/components/providers/connection-provider";
import { cn } from "@/lib/utils";

function NavLink({ item, collapsed, onNavigate, badge }: { item: NavItem; collapsed: boolean; onNavigate?: () => void; badge?: number }) {
  const pathname = usePathname();
  const active = isActive(pathname, item.href);
  const Icon = item.icon;
  const link = (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? item.label : undefined}
      className={cn(
        "group relative flex h-8 items-center gap-2.5 rounded-md text-[13px] font-medium text-sidebar-foreground/80 transition-colors hover:bg-sidebar-active hover:text-foreground",
        collapsed ? "w-9 justify-center" : "px-2.5",
        active && "bg-[linear-gradient(90deg,color-mix(in_oklch,var(--primary)_16%,transparent),transparent)] text-foreground shadow-[inset_0_1px_0_var(--edge)]",
      )}
    >
      {active && <span aria-hidden className="absolute -left-2 top-1.5 bottom-1.5 w-0.5 rounded-full bg-primary shadow-[0_0_10px_var(--primary)]" />}
      <Icon className={cn("size-4 shrink-0 transition-colors", active ? "text-primary" : "text-muted-foreground group-hover:text-foreground")} />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {badge ? (
        <span
          className={cn(
            "tabular rounded-full bg-critical/15 px-1.5 text-[10px] font-semibold leading-4 text-critical",
            collapsed ? "absolute -right-1 -top-1" : "ml-auto",
          )}
        >
          {badge > 99 ? "99+" : badge}
        </span>
      ) : null}
    </Link>
  );
  return collapsed ? (
    <Hint label={item.label} side="right">
      {link}
    </Hint>
  ) : (
    link
  );
}

function NavGroup({ title, items, collapsed, onNavigate, badges }: { title: string; items: NavItem[]; collapsed: boolean; onNavigate?: () => void; badges?: Record<string, number> }) {
  return (
    <div className="space-y-px">
      {collapsed ? <div className="mx-auto my-2 h-px w-6 bg-sidebar-border" /> : <p className="px-2.5 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">{title}</p>}
      {items.map((item) => (
        <NavLink key={item.href} item={item} collapsed={collapsed} onNavigate={onNavigate} badge={badges?.[item.href]} />
      ))}
    </div>
  );
}

export function SidebarContent({
  collapsed = false,
  onToggle,
  onNavigate,
  openAlerts = 0,
}: {
  collapsed?: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
  openAlerts?: number;
}) {
  const router = useRouter();
  const { connection } = useConnection();
  const badges = openAlerts ? { "/alerts": openAlerts } : undefined;

  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-14 shrink-0 items-center", collapsed ? "justify-center" : "justify-between px-4")}>
        <Link href="/ask" onClick={onNavigate} aria-label="InsightX home" className="rounded-md">
          <Logo collapsed={collapsed} />
        </Link>
        {onToggle && !collapsed && (
          <Hint label="Collapse sidebar (Ctrl+B)" side="right">
            <Button variant="ghost" size="icon-sm" onClick={onToggle} aria-label="Collapse sidebar" className="text-muted-foreground">
              <PanelLeftClose />
            </Button>
          </Hint>
        )}
      </div>

      <div className={cn("shrink-0", collapsed ? "px-2.5" : "px-3")}>
        {collapsed ? (
          <Hint label="New chat" side="right">
            <Button size="icon" variant="outline" aria-label="New chat" className="w-9" onClick={() => { router.push("/ask"); onNavigate?.(); }}>
              <Plus />
            </Button>
          </Hint>
        ) : (
          <Button variant="outline" className="w-full justify-start gap-2 bg-background/40" onClick={() => { router.push("/ask"); onNavigate?.(); }}>
            <Plus /> New chat
            <kbd className="ml-auto rounded border px-1 font-mono text-[10px] text-muted-foreground">N</kbd>
          </Button>
        )}
      </div>

      <div className={cn("scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto pb-3", collapsed ? "items-center px-2.5" : "px-3")}>
        <NavGroup title="Workspace" items={NAV_MAIN} collapsed={collapsed} onNavigate={onNavigate} badges={badges} />
        <NavGroup title="Monitor" items={NAV_MONITOR} collapsed={collapsed} onNavigate={onNavigate} />
        <NavGroup title="LLMOps" items={NAV_LLMOPS} collapsed={collapsed} onNavigate={onNavigate} />
        <NavGroup title="Settings" items={NAV_SETTINGS} collapsed={collapsed} onNavigate={onNavigate} />
        {!collapsed && (
          <div className="mt-4 border-t border-sidebar-border pt-3">
            <p className="px-2.5 pb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">Chats</p>
            <div className="-mx-1">
              <React.Suspense fallback={null}>
                <ChatList onNavigate={onNavigate} />
              </React.Suspense>
            </div>
          </div>
        )}
      </div>

      <div className={cn("shrink-0 border-t border-sidebar-border", collapsed ? "flex flex-col items-center gap-2 py-3" : "p-3")}>
        {collapsed ? (
          <>
            <Hint label={connection ? `Connected: ${connection.label}` : "No database connected"} side="right">
              <Link href="/connect" onClick={onNavigate} aria-label="Database" className="relative flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-sidebar-active hover:text-foreground">
                <Database className="size-4" />
                <span className={cn("absolute right-2 top-2 size-1.5 rounded-full", connection ? "bg-success" : "bg-warning")} />
              </Link>
            </Hint>
            {onToggle && (
              <Hint label="Expand sidebar (Ctrl+B)" side="right">
                <Button variant="ghost" size="icon-sm" onClick={onToggle} aria-label="Expand sidebar" className="text-muted-foreground">
                  <PanelLeftOpen />
                </Button>
              </Hint>
            )}
          </>
        ) : (
          <Link
            href="/connect"
            onClick={onNavigate}
            className="flex items-center gap-2.5 rounded-lg border border-sidebar-border bg-background/30 px-3 py-2 transition-colors hover:bg-sidebar-active"
          >
            <span className="relative flex size-7 shrink-0 items-center justify-center rounded-md bg-accent">
              <Database className="size-3.5" />
              <span className={cn("absolute -right-0.5 -top-0.5 size-2 rounded-full ring-2 ring-sidebar", connection ? "bg-success" : "bg-warning")} />
            </span>
            <span className="min-w-0 leading-tight">
              <span className="block truncate text-[13px] font-medium">{connection ? connection.label : "No database"}</span>
              <span className="block text-[11px] text-muted-foreground">{connection ? "Connected" : "Connect one to start"}</span>
            </span>
          </Link>
        )}
      </div>
    </div>
  );
}
