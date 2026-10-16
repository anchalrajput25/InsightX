"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatNumber, humanize, toNumber } from "@/lib/format";
import type { Row } from "@/lib/types";
import { cn } from "@/lib/utils";

function csv(columns: string[], rows: Row[]): string {
  const cell = (value: unknown) => {
    const text = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns.map(cell).join(","), ...rows.map((row) => columns.map((column) => cell(row[column])).join(","))].join("\n");
}

export function downloadCsv(columns: string[], rows: Row[], name = "insightx-result.csv") {
  const blob = new Blob([csv(columns, rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function display(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") return formatNumber(value, { compactFrom: 1e12 });
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function DataTable({
  columns,
  rows,
  pageSize = 10,
  className,
  exportName,
}: {
  columns: string[];
  rows: Row[];
  pageSize?: number;
  className?: string;
  exportName?: string;
}) {
  const [sort, setSort] = React.useState<{ column: string; desc: boolean } | null>(null);
  const [page, setPage] = React.useState(0);
  const cols = columns.length ? columns : Object.keys(rows[0] ?? {});

  const numeric = React.useMemo(() => {
    const result: Record<string, boolean> = {};
    for (const column of cols) {
      const values = rows.slice(0, 50).map((row) => row[column]).filter((value) => value !== null && value !== undefined);
      result[column] = values.length > 0 && values.every((value) => toNumber(value) !== null);
    }
    return result;
  }, [cols, rows]);

  const sorted = React.useMemo(() => {
    if (!sort) return rows;
    const direction = sort.desc ? -1 : 1;
    return [...rows].sort((a, b) => {
      const x = a[sort.column];
      const y = b[sort.column];
      const nx = toNumber(x);
      const ny = toNumber(y);
      if (nx !== null && ny !== null) return (nx - ny) * direction;
      return String(x ?? "").localeCompare(String(y ?? "")) * direction;
    });
  }, [rows, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = sorted.slice(current * pageSize, current * pageSize + pageSize);

  const toggle = (column: string) => {
    setPage(0);
    setSort((previous) =>
      previous?.column !== column ? { column, desc: numeric[column] ?? false } : previous.desc ? { column, desc: false } : null,
    );
  };

  return (
    <div className={cn("overflow-hidden rounded-lg border", className)}>
      <Table>
        <TableHeader>
          <TableRow className="bg-surface hover:bg-surface">
            {cols.map((column) => (
              <TableHead key={column} className={cn(numeric[column] && "text-right")}>
                <button
                  type="button"
                  onClick={() => toggle(column)}
                  className={cn("inline-flex items-center gap-1 hover:text-foreground", numeric[column] && "flex-row-reverse")}
                  aria-label={`Sort by ${humanize(column)}`}
                >
                  {humanize(column)}
                  {sort?.column === column && (sort.desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                </button>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((row, index) => (
            <TableRow key={index}>
              {cols.map((column) => (
                <TableCell
                  key={column}
                  className={cn("max-w-[320px] truncate", numeric[column] && "tabular text-right font-mono text-[12.5px]")}
                  title={display(row[column])}
                >
                  {display(row[column])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="flex items-center justify-between gap-2 border-t bg-surface px-3 py-1.5 text-xs text-muted-foreground">
        <span className="tabular">
          {rows.length.toLocaleString()} row{rows.length === 1 ? "" : "s"}
          {pages > 1 && ` · page ${current + 1} of ${pages}`}
        </span>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="xs" onClick={() => downloadCsv(cols, sorted, exportName)} aria-label="Download CSV">
            <Download /> CSV
          </Button>
          {pages > 1 && (
            <>
              <Button variant="ghost" size="icon-sm" className="size-7" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page">
                <ChevronLeft />
              </Button>
              <Button variant="ghost" size="icon-sm" className="size-7" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page">
                <ChevronRight />
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
