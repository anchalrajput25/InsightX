"use client";

// Read the app's colour tokens (oklch CSS variables) as plain hex colours,
// because the chart canvas and exported SVG need concrete colours.
// Re-reads when the light/dark theme changes.

import * as React from "react";
import type { ChartTheme } from "@/lib/chart-intel/options";

let probe: CanvasRenderingContext2D | null = null;

/** Paint the colour over the backdrop and read the pixel back, so see-through colours become solid. */
function toHex(cssColor: string, fallback: string, backdrop = "#ffffff"): string {
  if (typeof document === "undefined") return fallback;
  probe ??= document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  if (!probe || !cssColor) return fallback;
  probe.fillStyle = backdrop;
  probe.fillRect(0, 0, 1, 1);
  probe.fillStyle = fallback;
  probe.fillStyle = cssColor;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((v) => (v ?? 0).toString(16).padStart(2, "0")).join("")}`;
}

function readVar(style: CSSStyleDeclaration, name: string, fallback: string, backdrop?: string): string {
  return toHex(style.getPropertyValue(name).trim(), fallback, backdrop);
}

const FALLBACK: ChartTheme = {
  series: ["#7c5cff", "#e0a060", "#4fae7d", "#d86fb0", "#4f8fd0", "#9b8cff", "#c9a227", "#5bb5b0"],
  text: "#1f1f24",
  muted: "#6b6b76",
  grid: "#ececf0",
  axis: "#8a8a94",
  surface: "#ffffff",
  popover: "#ffffff",
  border: "#e5e5ea",
  highlight: "#e0a060",
  font: "inherit",
};

export function readChartTheme(): ChartTheme {
  if (typeof window === "undefined") return FALLBACK;
  const style = getComputedStyle(document.documentElement);
  const surface = readVar(style, "--card", FALLBACK.surface);
  const base = [1, 2, 3, 4, 5].map((i) => readVar(style, `--chart-${i}`, FALLBACK.series[i - 1] as string, surface));
  return {
    // five brand colours plus three softer extras for charts with more series
    series: [...base, "#9b8cff", "#c9a227", "#5bb5b0", "#e07a7a", "#7aa6e0"],
    text: readVar(style, "--foreground", FALLBACK.text, surface),
    muted: readVar(style, "--muted-foreground", FALLBACK.muted, surface),
    grid: readVar(style, "--chart-grid", FALLBACK.grid, surface),
    axis: readVar(style, "--chart-axis", FALLBACK.axis, surface),
    surface,
    popover: readVar(style, "--popover", FALLBACK.popover, surface),
    border: readVar(style, "--border", FALLBACK.border, surface),
    highlight: base[2] as string,
    font: getComputedStyle(document.body).fontFamily || "inherit",
  };
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = React.useState<ChartTheme>(FALLBACK);
  React.useEffect(() => {
    setTheme(readChartTheme());
    const observer = new MutationObserver(() => setTheme(readChartTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}
