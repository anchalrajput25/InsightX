"use client";

import * as React from "react";
import { ChevronRight } from "lucide-react";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  if (isPlainObject(value)) return Object.keys(value).length === 0;
  return false;
}

function Scalar({ value }: { value: unknown }) {
  if (typeof value === "boolean")
    return <span className={cn("font-medium", value ? "text-success" : "text-critical")}>{value ? "Yes" : "No"}</span>;
  if (typeof value === "number") return <span className="tabular font-medium">{Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4).replace(/0+$/, "")}</span>;
  return <span className="break-words whitespace-pre-wrap">{String(value)}</span>;
}

/**
 * Readable view of any JSON value: short lists become chips, objects become
 * labelled rows, and deep parts fold away. Used where a step's output has no
 * dedicated layout.
 */
export function ValueView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const [open, setOpen] = React.useState(depth < 1);

  if (isEmpty(value)) return <span className="text-muted-foreground">None</span>;
  if (!Array.isArray(value) && !isPlainObject(value)) return <Scalar value={value} />;

  if (Array.isArray(value)) {
    const simple = value.every((item) => !Array.isArray(item) && !isPlainObject(item));
    if (simple && value.every((item) => String(item).length <= 60)) {
      return (
        <div className="flex flex-wrap gap-1.5">
          {value.map((item, index) => (
            <span key={index} className="rounded-md border bg-muted/50 px-1.5 py-0.5 font-mono text-[11.5px]">
              {String(item)}
            </span>
          ))}
        </div>
      );
    }
    if (simple) {
      return (
        <ul className="list-disc space-y-1 pl-4">
          {value.map((item, index) => (
            <li key={index}>
              <Scalar value={item} />
            </li>
          ))}
        </ul>
      );
    }
  }

  const entries = Array.isArray(value) ? value.map((item, index) => [`Item ${index + 1}`, item] as const) : Object.entries(value);
  const body = (
    <dl className="divide-y divide-border/60 rounded-lg border bg-background/40">
      {entries.map(([key, item]) => (
        <div key={key} className="grid gap-1 px-3 py-2 sm:grid-cols-[160px_1fr] sm:gap-3">
          <dt className="text-xs font-medium text-muted-foreground">{Array.isArray(value) ? key : humanize(key)}</dt>
          <dd className="min-w-0 text-[13px]">
            <ValueView value={item} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
  if (depth === 0) return body;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="inline-flex cursor-pointer items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight className={cn("size-3.5 transition-transform duration-200", open && "rotate-90")} />
        {open ? "Hide" : "Show"} {entries.length} {entries.length === 1 ? "field" : "fields"}
      </button>
      {open && <div className="mt-1.5">{body}</div>}
    </div>
  );
}
