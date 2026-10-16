"use client";

import * as React from "react";

const NUMBER = /^(\D*?)(-?[\d,]*\.?\d+)(.*)$/s;

function parse(value: React.ReactNode) {
  if (typeof value === "number") return Number.isFinite(value) ? { prefix: "", target: value, suffix: "", decimals: String(value).split(".")[1]?.length ?? 0, commas: false } : null;
  if (typeof value !== "string") return null;
  const match = NUMBER.exec(value.trim());
  if (!match) return null;
  const raw = match[2] ?? "";
  const prefix = match[1] ?? "";
  const suffix = match[3] ?? "";
  const target = Number(raw.replace(/,/g, ""));
  if (!Number.isFinite(target) || /\d/.test(suffix)) return null;
  return { prefix, target, suffix, decimals: raw.split(".")[1]?.length ?? 0, commas: raw.includes(",") };
}

/**
 * Number ticker (21st.dev style): counts up to the value when it first shows or changes.
 * Anything that is not a plain number (or a string starting with one) is shown as is.
 */
export function NumberTicker({ value, duration = 900 }: { value: React.ReactNode; duration?: number }) {
  const parsed = parse(value);
  const target = parsed?.target;
  const [shown, setShown] = React.useState<number | null>(null);
  const from = React.useRef(0);

  React.useEffect(() => {
    if (target === undefined) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(target);
      from.current = target;
      return;
    }
    const start = performance.now();
    const origin = from.current;
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(2, -10 * t);
      const current = t >= 1 ? target : origin + (target - origin) * eased;
      setShown(current);
      if (t < 1) frame = requestAnimationFrame(step);
      else from.current = target;
    };
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
      from.current = target;
    };
  }, [target, duration]);

  if (!parsed) return <>{value}</>;
  const number = shown ?? parsed.target;
  const text = parsed.commas
    ? number.toLocaleString("en-US", { minimumFractionDigits: parsed.decimals, maximumFractionDigits: parsed.decimals })
    : number.toFixed(parsed.decimals);
  return (
    <span className="tabular" aria-label={typeof value === "string" ? value : String(value)}>
      <span aria-hidden>
        {parsed.prefix}
        {text}
        {parsed.suffix}
      </span>
    </span>
  );
}
