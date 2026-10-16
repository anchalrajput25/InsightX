"use client";

import { Hint } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const WEEKS = 18;

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

/** A calendar of how many questions were asked each day (last ~4 months). */
export function ActivityGrid({ activity }: { activity: { ts: number; kind: string }[] }) {
  const counts = new Map<string, { questions: number; investigations: number }>();
  for (const item of activity) {
    const key = dayKey(new Date(item.ts * 1000));
    const entry = counts.get(key) ?? { questions: 0, investigations: 0 };
    if (item.kind === "investigation") entry.investigations += 1;
    else entry.questions += 1;
    counts.set(key, entry);
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start = new Date(today);
  start.setDate(start.getDate() - (WEEKS * 7 - 1) - start.getDay());
  const days: Date[] = [];
  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) days.push(new Date(d));
  const max = Math.max(1, ...Array.from(counts.values(), (c) => c.questions + c.investigations));
  const level = (n: number) => (n === 0 ? 0 : Math.min(4, Math.ceil((n / max) * 4)));
  const columns: Date[][] = [];
  days.forEach((day, index) => {
    const column = Math.floor(index / 7);
    (columns[column] ??= []).push(day);
  });
  const total = activity.length;

  return (
    <div className="space-y-2">
      <div className="scrollbar-thin flex gap-[3px] overflow-x-auto pb-1" role="img" aria-label={`${total} questions asked in the last ${WEEKS} weeks`}>
        {columns.map((column, index) => (
          <div key={index} className="flex flex-col gap-[3px]">
            {column.map((day) => {
              const c = counts.get(dayKey(day)) ?? { questions: 0, investigations: 0 };
              const n = c.questions + c.investigations;
              return (
                <Hint key={day.toISOString()} label={`${day.toLocaleDateString("en-US", { month: "short", day: "numeric" })}: ${n === 0 ? "nothing asked" : `${c.questions} question${c.questions === 1 ? "" : "s"}, ${c.investigations} investigation${c.investigations === 1 ? "" : "s"}`}`}>
                  <span
                    className={cn(
                      "block size-[11px] rounded-[3px]",
                      ["bg-muted", "bg-primary/25", "bg-primary/45", "bg-primary/70", "bg-primary"][level(n)],
                    )}
                  />
                </Hint>
              );
            })}
          </div>
        ))}
      </div>
      <div className="flex items-center justify-end gap-1.5 text-[11px] text-muted-foreground">
        Less
        {["bg-muted", "bg-primary/25", "bg-primary/45", "bg-primary/70", "bg-primary"].map((tone) => (
          <span key={tone} className={cn("size-[10px] rounded-[2px]", tone)} />
        ))}
        More
      </div>
    </div>
  );
}
