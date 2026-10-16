"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/client";
import { AuthProvider } from "@/components/providers/auth-provider";
import { ConnectionProvider } from "@/components/providers/connection-provider";

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 20_000,
            refetchOnWindowFocus: false,
            retry: (count, error) => !(error instanceof ApiError && error.status > 0 && error.status < 500) && count < 1,
          },
        },
      }),
  );
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <QueryClientProvider client={client}>
        <TooltipProvider delayDuration={250}>
          <AuthProvider>
            <ConnectionProvider>{children}</ConnectionProvider>
          </AuthProvider>
        </TooltipProvider>
        <Toaster
          position="bottom-right"
          theme="system"
          toastOptions={{
            classNames: {
              toast: "!bg-popover !text-popover-foreground !border-border !rounded-lg !text-[13px]",
              description: "!text-muted-foreground",
            },
          }}
        />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
