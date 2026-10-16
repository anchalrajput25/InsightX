"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight, Database, LogOut, Menu, Moon, Search, Sun, UserRound } from "lucide-react";
import { useTheme } from "next-themes";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AlertsBell } from "@/components/layout/alerts-bell";
import { ALL_NAV, isActive } from "@/components/layout/nav";
import { useAuth } from "@/components/providers/auth-provider";
import { useConnection } from "@/components/providers/connection-provider";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

export function Topbar({ onOpenMenu, onOpenSearch }: { onOpenMenu: () => void; onOpenSearch: () => void }) {
  const pathname = usePathname();
  const { connection } = useConnection();
  const { displayName, email, state, signOut } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const page = ALL_NAV.find((item) => isActive(pathname, item.href));
  const canSignOut = state.phase === "ready" && state.required;

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-3 backdrop-blur-md supports-[backdrop-filter]:bg-background/65 sm:px-5">
      <Button variant="ghost" size="icon-sm" className="lg:hidden" onClick={onOpenMenu} aria-label="Open menu">
        <Menu />
      </Button>

      <nav aria-label="Breadcrumb" className="hidden min-w-0 items-center gap-1.5 text-[13px] sm:flex">
        <span className="text-muted-foreground">InsightX</span>
        <ChevronRight className="size-3.5 text-muted-foreground/60" />
        <span className="truncate font-medium">{page?.label ?? "Home"}</span>
      </nav>

      <button
        type="button"
        onClick={onOpenSearch}
        className="mx-auto flex h-8 w-full max-w-md cursor-pointer items-center gap-2 rounded-md border bg-surface/70 px-2.5 text-[13px] text-muted-foreground shadow-[inset_0_1px_0_var(--edge)] transition-[color,border-color,box-shadow] duration-200 hover:border-primary/30 hover:text-foreground hover:shadow-[inset_0_1px_0_var(--edge),0_0_0_3px_color-mix(in_oklch,var(--primary)_10%,transparent)] sm:ml-6"
      >
        <Search className="size-3.5" />
        <span className="truncate">Search or ask a question…</span>
        <kbd className="ml-auto hidden rounded border bg-background px-1.5 font-mono text-[10px] sm:inline">Ctrl K</kbd>
      </button>

      <div className="ml-auto flex items-center gap-1 sm:ml-0">
        <Link
          href="/connect"
          className={cn(
            "hidden h-8 max-w-56 items-center gap-2 rounded-md border px-2.5 text-xs transition-colors hover:bg-accent md:flex",
            !connection && "border-warning/40 text-warning",
          )}
        >
          <span className="relative flex">
            <Database className="size-3.5" />
          </span>
          <span className={cn("size-1.5 shrink-0 rounded-full", connection ? "bg-success" : "bg-warning")} aria-hidden />
          <span className="truncate">{connection ? connection.label : "Connect a database"}</span>
        </Link>

        <AlertsBell />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" aria-label="Account menu" className="ml-1 rounded-full">
              <Avatar className="size-8">
                <AvatarFallback className="text-[11px] font-semibold">{initials(displayName)}</AvatarFallback>
              </Avatar>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-60">
            <DropdownMenuLabel className="font-normal">
              <p className="truncate text-sm font-medium">{displayName}</p>
              <p className="truncate text-xs text-muted-foreground">{email || "Sign-in is turned off on this server"}</p>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/account">
                <UserRound /> Account
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/connect">
                <Database /> Database
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}>
              {resolvedTheme === "dark" ? <Sun /> : <Moon />} {resolvedTheme === "dark" ? "Light" : "Dark"} theme
            </DropdownMenuItem>
            {canSignOut && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={signOut}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
