// Number, date and text formatting shared by every page.

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const twoDecimals = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) return Number(value);
  return null;
}

/** 1234 -> "1,234", 12345 -> "12.3K", 1.5 -> "1.5" */
export function formatNumber(value: unknown, options: { compactFrom?: number } = {}): string {
  const number = toNumber(value);
  if (number === null) return value === null || value === undefined ? "-" : String(value);
  const compactFrom = options.compactFrom ?? 10_000;
  if (Math.abs(number) >= compactFrom) return compact.format(number);
  if (Number.isInteger(number)) return whole.format(number);
  return twoDecimals.format(number);
}

export function formatPercent(value: number | null | undefined, signed = true): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  const text = `${Math.abs(value) >= 100 ? Math.round(value) : value.toFixed(1)}%`;
  return signed && value > 0 ? `+${text}` : text;
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function toDate(value: number | string | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = typeof value === "number" ? new Date(value < 1e12 ? value * 1000 : value) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function timeAgo(value: number | string | null | undefined, now = Date.now()): string {
  const date = toDate(value);
  if (!date) return "never";
  const seconds = (now - date.getTime()) / 1000;
  const future = seconds < 0;
  const abs = Math.abs(seconds);
  const units: [number, string][] = [
    [86400 * 365, "year"],
    [86400 * 30, "month"],
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ];
  for (const [size, unit] of units) {
    if (abs >= size) {
      const count = Math.floor(abs / size);
      const text = `${count} ${unit}${count === 1 ? "" : "s"}`;
      return future ? `in ${text}` : `${text} ago`;
    }
  }
  return future ? "in a moment" : "just now";
}

export function formatDateTime(value: number | string | null | undefined): string {
  const date = toDate(value);
  if (!date) return "-";
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatDay(value: number | string | null | undefined): string {
  const date = toDate(value);
  if (!date) return "-";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** "customer_state" -> "Customer state" */
export function humanize(name: string): string {
  const text = String(name).replace(/[_.]+/g, " ").replace(/\s+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

const EMOJI = /[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}\u{FE0F}\u{200D}]/gu;

/** Backend text sometimes starts with an emoji marker; this UI shows none. */
export function stripEmoji(text: string): string {
  return String(text ?? "").replace(EMOJI, "").replace(/^\s+/, "");
}

export function truncate(text: string, length: number): string {
  const clean = String(text ?? "").trim();
  return clean.length > length ? `${clean.slice(0, length - 1).trimEnd()}…` : clean;
}

export function initials(name: string): string {
  const parts = String(name || "?").trim().split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}
