"use client";

import * as React from "react";
import { Code2, Maximize2, Trash2 } from "lucide-react";
import { AutoChart } from "@/components/charts/auto-chart";
import { CodeBlock } from "@/components/common/code-block";
import { DataTable } from "@/components/common/data-table";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Hint } from "@/components/ui/tooltip";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Row } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ChartCard({
  title,
  subtitle,
  columns,
  rows,
  kind,
  sql,
  error,
  onDelete,
  className,
  height = 240,
}: {
  title: string;
  subtitle?: string;
  columns: string[];
  rows: Row[];
  kind?: string;
  sql?: string;
  error?: string;
  onDelete?: () => void;
  className?: string;
  height?: number;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <section className={cn("group flex flex-col rounded-xl border bg-card", className)}>
      <header className="flex items-start justify-between gap-3 px-4 pb-2 pt-3.5">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-medium" title={title}>
            {title}
          </h3>
          {subtitle && <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-0.5 opacity-70 transition-opacity group-hover:opacity-100">
          <Hint label="Open with data and SQL">
            <Button variant="ghost" size="icon-sm" className="size-7" aria-label={`Open ${title}`} onClick={() => setOpen(true)}>
              <Maximize2 className="size-3.5" />
            </Button>
          </Hint>
          {onDelete && (
            <Hint label="Remove chart">
              <Button variant="ghost" size="icon-sm" className="size-7 hover:text-critical" aria-label={`Remove ${title}`} onClick={onDelete}>
                <Trash2 className="size-3.5" />
              </Button>
            </Hint>
          )}
        </div>
      </header>
      <div className="flex-1 px-4 pb-4">
        {error ? (
          <p className="flex h-full min-h-24 items-center justify-center rounded-lg border border-dashed px-4 text-center text-xs text-muted-foreground">{error}</p>
        ) : rows.length === 0 ? (
          <p className="flex h-full min-h-24 items-center justify-center rounded-lg border border-dashed text-xs text-muted-foreground">No rows to show.</p>
        ) : (
          <AutoChart columns={columns} rows={rows} preferred={kind || undefined} height={height} />
        )}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
          </DialogHeader>
          <Tabs defaultValue="chart">
            <TabsList className="mb-4">
              <TabsTrigger value="chart">Chart</TabsTrigger>
              <TabsTrigger value="data">Data</TabsTrigger>
              {sql && (
                <TabsTrigger value="sql">
                  <Code2 className="size-3.5" /> SQL
                </TabsTrigger>
              )}
            </TabsList>
            <TabsContent value="chart">{rows.length ? <AutoChart columns={columns} rows={rows} preferred={kind || undefined} height={360} /> : null}</TabsContent>
            <TabsContent value="data">
              <DataTable columns={columns} rows={rows} pageSize={10} exportName={`${title.slice(0, 40)}.csv`} />
            </TabsContent>
            {sql && (
              <TabsContent value="sql">
                <CodeBlock code={sql} />
              </TabsContent>
            )}
          </Tabs>
        </DialogContent>
      </Dialog>
    </section>
  );
}
