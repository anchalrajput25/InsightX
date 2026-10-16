"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const KEYWORDS =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LIMIT|JOIN|LEFT JOIN|RIGHT JOIN|INNER JOIN|ON|AS|AND|OR|NOT|IN|IS|NULL|COUNT|SUM|AVG|MIN|MAX|DISTINCT|HAVING|CASE|WHEN|THEN|ELSE|END|WITH|DESC|ASC|BY|TOP|CAST|COALESCE|OVER|PARTITION)\b/gi;

function highlight(sql: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of sql.matchAll(KEYWORDS)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(sql.slice(last, index));
    parts.push(
      <span key={index} className="text-primary">
        {match[0]}
      </span>,
    );
    last = index + match[0].length;
  }
  parts.push(sql.slice(last));
  return parts;
}

export function CodeBlock({ code, className, label = "SQL" }: { code: string; className?: string; label?: string }) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className={cn("overflow-hidden rounded-lg border bg-surface", className)}>
      <div className="flex items-center justify-between border-b px-3 py-1.5">
        <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</span>
        <Button variant="ghost" size="xs" onClick={copy} aria-label="Copy SQL">
          {copied ? <Check className="text-success" /> : <Copy />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="max-h-72 overflow-auto p-3 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap scrollbar-thin">
        <code>{highlight(code)}</code>
      </pre>
    </div>
  );
}
