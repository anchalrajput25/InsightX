// Build the Apache ECharts option for a plan and its prepared data:
// themed colours, unit-aware axes, tooltips, zoom, legend toggles.

import type { EChartsOption } from "echarts";
import { humanize } from "@/lib/format";
import { axisLabel, formatOf, formatValue, measureTitle, type ValueFormat } from "@/lib/chart-intel/format";
import type { Prepared } from "@/lib/chart-intel/prepare";
import type { SmartPlan } from "@/lib/chart-intel/recommend";

export interface ChartTheme {
  series: string[];
  text: string;
  muted: string;
  grid: string;
  axis: string;
  surface: string;
  popover: string;
  border: string;
  highlight: string;
  font: string;
}

/** Things the option builder needs to know about the current view. */
export interface OptionContext {
  theme: ChartTheme;
  logScale: boolean;
  /** Category picked by click-to-filter / cross-filter (drawn in the accent colour). */
  selected: string | null;
  compact: boolean;
}

type AnyRecord = Record<string, unknown>;

function shortText(value: string, length: number): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function dateLabel(value: string, grain: string | null | undefined): string {
  if (grain === "year" || grain === "quarter") return value;
  const text = value.length === 7 ? `${value}-01` : value;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return value;
  if (grain === "month") return date.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  if (grain === "time") return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
}

function tooltipBase(theme: ChartTheme): AnyRecord {
  return {
    confine: true,
    backgroundColor: theme.popover,
    borderColor: theme.border,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: theme.text, fontSize: 12, fontFamily: theme.font },
    extraCssText: "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.18);",
  };
}

function valueAxis(theme: ChartTheme, format: ValueFormat, extra: AnyRecord = {}): AnyRecord {
  return {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: theme.grid } },
    axisLabel: { color: theme.axis, fontSize: 11, formatter: (v: number) => axisLabel(v, format) },
    nameTextStyle: { color: theme.muted, fontSize: 11 },
    ...extra,
  };
}

function categoryAxis(theme: ChartTheme, data: string[], formatter: (v: string) => string, extra: AnyRecord = {}): AnyRecord {
  return {
    type: "category",
    data,
    axisLine: { lineStyle: { color: theme.grid } },
    axisTick: { show: false },
    axisLabel: { color: theme.axis, fontSize: 11, formatter, hideOverlap: true },
    ...extra,
  };
}

function legend(theme: ChartTheme, names: string[]): AnyRecord | undefined {
  if (names.length < 2) return undefined;
  return {
    type: "scroll",
    top: 0,
    left: 0,
    icon: "roundRect",
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: theme.muted, fontSize: 11 },
    pageTextStyle: { color: theme.muted },
    pageIconColor: theme.text,
    pageIconInactiveColor: theme.grid,
    data: names,
  };
}

function zoom(theme: ChartTheme, orient: "x" | "y", count: number, compact: boolean): AnyRecord[] {
  if (count <= 12) return [];
  const slider = {
    type: "slider",
    [orient === "x" ? "xAxisIndex" : "yAxisIndex"]: 0,
    height: orient === "x" ? 16 : undefined,
    width: orient === "y" ? 14 : undefined,
    bottom: orient === "x" ? 4 : undefined,
    right: orient === "y" ? 4 : undefined,
    borderColor: "transparent",
    backgroundColor: "transparent",
    fillerColor: `${theme.series[0]}22`,
    dataBackground: { lineStyle: { color: theme.grid }, areaStyle: { color: theme.grid } },
    handleStyle: { color: theme.surface, borderColor: theme.axis },
    moveHandleSize: 0,
    textStyle: { color: theme.muted, fontSize: 10 },
    showDetail: false,
  };
  const inside = { type: "inside", [orient === "x" ? "xAxisIndex" : "yAxisIndex"]: 0, zoomOnMouseWheel: "shift", moveOnMouseMove: true };
  return compact ? [inside] : [inside, slider];
}

function categoryOption(plan: SmartPlan, data: Extract<Prepared, { type: "category" }>, ctx: OptionContext): EChartsOption {
  const { theme } = ctx;
  const profiles = new Map(plan.profile.columns.map((c) => [c.name, c]));
  const xProfile = profiles.get(plan.x);
  const temporal = plan.xRole === "temporal";
  const labelOf = (v: string) => (temporal ? dateLabel(v, xProfile?.grain) : shortText(v, plan.horizontal ? 18 : 14));
  const formats = data.series.map((s) => formatOf(profiles.get(s.measure)));
  const mainFormat = formats[0] ?? { unit: "none", fraction: false };
  const color = (i: number) => theme.series[i % theme.series.length] as string;
  const names = data.series.map((s) => (plan.split ? s.name : humanize(s.name)));
  const kind = plan.kind;

  if (kind === "donut") {
    const s = data.series[0];
    const items = data.categories.map((name, i) => ({
      name,
      value: s?.values[i] ?? 0,
      itemStyle: { color: color(i), opacity: ctx.selected && ctx.selected !== name ? 0.35 : 1 },
    }));
    return {
      color: theme.series,
      tooltip: {
        ...tooltipBase(theme),
        trigger: "item",
        formatter: (p: unknown) => {
          const item = p as { name: string; value: number; percent: number; marker: string };
          return `${item.marker}${item.name}<br/><b>${formatValue(item.value, mainFormat)}</b> &nbsp;${item.percent.toFixed(1)}%`;
        },
      },
      legend: {
        type: "scroll",
        orient: ctx.compact ? "horizontal" : "vertical",
        right: 0,
        top: ctx.compact ? undefined : "middle",
        bottom: ctx.compact ? 0 : undefined,
        icon: "circle",
        itemWidth: 8,
        textStyle: { color: theme.muted, fontSize: 11 },
        formatter: (name: string) => shortText(name, 18),
      },
      series: [
        {
          type: "pie",
          radius: ["52%", "78%"],
          center: ctx.compact ? ["50%", "45%"] : ["38%", "50%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: theme.surface, borderWidth: 2, borderRadius: 4 },
          label: { show: !ctx.compact, color: theme.muted, fontSize: 11, formatter: (p: { percent?: number }) => `${(p.percent ?? 0).toFixed(1)}%` },
          labelLine: { lineStyle: { color: theme.grid } },
          emphasis: { scaleSize: 6, label: { show: true, fontWeight: "bold" } },
          data: items,
        },
      ],
    } as EChartsOption;
  }

  const stacked = kind === "stacked-bar" || kind === "stacked-area";
  const barLike = kind === "bar" || kind === "stacked-bar";
  const horizontal = barLike && plan.horizontal && !temporal;
  const many = data.categories.length;

  const series = data.series.map((s, i) => {
    const isComboLine = kind === "combo" && i > 0;
    const asBar = barLike || (kind === "combo" && i === 0);
    const asArea = kind === "area" || kind === "stacked-area";
    const values = s.values.map((v, j) => {
      const name = data.categories[j] as string;
      if (asBar && data.series.length === 1 && ctx.selected) {
        return { value: v, itemStyle: { color: name === ctx.selected ? theme.highlight : color(0), opacity: name === ctx.selected ? 1 : 0.45 } };
      }
      return v;
    });
    const common: AnyRecord = {
      name: names[i],
      data: values,
      emphasis: { focus: "series" },
      itemStyle: { color: color(i) },
    };
    if (asBar) {
      return {
        ...common,
        type: "bar",
        stack: stacked ? "total" : undefined,
        barMaxWidth: horizontal ? 22 : 42,
        itemStyle: {
          color: color(i),
          borderRadius: stacked ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        },
      };
    }
    return {
      ...common,
      type: "line",
      yAxisIndex: isComboLine ? 1 : 0,
      smooth: 0.25,
      showSymbol: many <= 30,
      symbolSize: 5,
      connectNulls: false,
      stack: kind === "stacked-area" ? "total" : undefined,
      lineStyle: { width: 2, color: color(i) },
      areaStyle: asArea
        ? {
            opacity: kind === "stacked-area" ? 0.55 : 1,
            color:
              kind === "stacked-area"
                ? color(i)
                : { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: `${color(i)}55` }, { offset: 1, color: `${color(i)}00` }] },
          }
        : undefined,
    };
  });

  const allPositive = data.series.every((s) => s.values.every((v) => v === null || v > 0));
  const useLog = ctx.logScale && allPositive && !barLike && kind !== "combo";
  const leftFormat = mainFormat;
  const yAxes: AnyRecord[] = [
    valueAxis(theme, leftFormat, {
      type: useLog ? "log" : "value",
      // Bars and areas must start at zero, or small gaps look huge.
      scale: !barLike && !useLog && kind === "line",
      axisLabel: {
        color: kind === "combo" ? color(0) : theme.axis,
        fontSize: 11,
        formatter: (v: number) => axisLabel(v, leftFormat),
      },
    }),
  ];
  if (kind === "combo") {
    yAxes.push(
      valueAxis(theme, formats[1] ?? leftFormat, {
        position: "right",
        axisLabel: { color: color(1), fontSize: 11, formatter: (v: number) => axisLabel(v, formats[1] ?? leftFormat) },
        splitLine: { show: false },
        alignTicks: true,
      }),
    );
  }

  const cats = categoryAxis(theme, data.categories, labelOf, {
    boundaryGap: barLike || kind === "combo",
    inverse: horizontal,
  });
  const legendNames = names;
  const hasLegend = legendNames.length > 1;
  const zoomBlocks = zoom(theme, horizontal ? "y" : "x", many, ctx.compact);
  const sliderX = zoomBlocks.length > 1 && !horizontal;
  const sliderY = zoomBlocks.length > 1 && horizontal;

  return {
    color: theme.series,
    animationDuration: 500,
    grid: {
      left: 4,
      right: kind === "combo" ? 8 : sliderY ? 28 : barLike ? 12 : 28,
      top: hasLegend ? 34 : 12,
      bottom: sliderX ? 30 : 4,
      containLabel: true,
    },
    legend: legend(theme, legendNames),
    tooltip: {
      ...tooltipBase(theme),
      trigger: "axis",
      axisPointer: { type: barLike || kind === "combo" ? "shadow" : "line", shadowStyle: { color: `${theme.grid}88` }, lineStyle: { color: theme.axis, type: "dashed" } },
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as { seriesIndex: number; marker: string; seriesName: string; value: unknown; name: string }[];
        if (!list.length) return "";
        const head = temporal ? dateLabel(list[0]?.name ?? "", xProfile?.grain) : (list[0]?.name ?? "");
        const lines = list
          .filter((p) => {
            const v = typeof p.value === "object" && p.value !== null ? (p.value as { value?: unknown }).value : p.value;
            return v !== null && v !== undefined && v !== "-";
          })
          .map((p) => {
            const raw = typeof p.value === "object" && p.value !== null ? (p.value as { value?: unknown }).value : p.value;
            const fmt = formats[plan.split ? 0 : p.seriesIndex] ?? leftFormat;
            return `<div style="display:flex;justify-content:space-between;gap:16px"><span>${p.marker}${p.seriesName}</span><b style="font-variant-numeric:tabular-nums">${formatValue(raw, fmt)}</b></div>`;
          });
        if (stacked && list.length > 1) {
          const total = list.reduce((sum, p) => {
            const raw = typeof p.value === "object" && p.value !== null ? (p.value as { value?: unknown }).value : p.value;
            return sum + (typeof raw === "number" ? raw : 0);
          }, 0);
          lines.push(`<div style="display:flex;justify-content:space-between;gap:16px;border-top:1px solid ${theme.border};margin-top:4px;padding-top:4px"><span>Total</span><b>${formatValue(total, leftFormat)}</b></div>`);
        }
        return `<div style="font-weight:600;margin-bottom:4px">${head}</div>${lines.join("")}`;
      },
    },
    dataZoom: zoomBlocks,
    xAxis: horizontal ? yAxes[0] : cats,
    yAxis: horizontal ? cats : yAxes,
    series,
  } as EChartsOption;
}

function pointOption(plan: SmartPlan, data: Extract<Prepared, { type: "points" }>, ctx: OptionContext): EChartsOption {
  const { theme } = ctx;
  const profiles = new Map(plan.profile.columns.map((c) => [c.name, c]));
  const fx = formatOf(profiles.get(data.xName));
  const fy = formatOf(profiles.get(data.yName));
  const fs = formatOf(data.sizeName ? profiles.get(data.sizeName) : undefined);
  const sizes = data.points.map((p) => p.size ?? 0);
  const maxSize = Math.max(...sizes.map(Math.abs), 1);
  const symbolSize = (value: number[]) => {
    if (!data.sizeName) return 9;
    return 6 + 38 * Math.sqrt(Math.abs(value[2] ?? 0) / maxSize);
  };
  const usual = data.points.filter((p) => !p.unusual).map((p) => [p.x, p.y, p.size ?? 0, p.label]);
  const unusual = data.points.filter((p) => p.unusual).map((p) => [p.x, p.y, p.size ?? 0, p.label]);
  const xs = data.points.map((p) => p.x);
  const series: AnyRecord[] = [
    {
      name: "Points",
      type: "scatter",
      data: usual,
      symbolSize,
      itemStyle: { color: theme.series[0], opacity: data.sizeName ? 0.6 : 0.8, borderColor: theme.surface, borderWidth: data.sizeName ? 1 : 0 },
      emphasis: { focus: "self", itemStyle: { opacity: 1 } },
    },
  ];
  if (unusual.length) {
    series.push({
      name: "Unusual",
      type: "scatter",
      data: unusual,
      symbolSize,
      itemStyle: { color: theme.highlight, opacity: 0.9, borderColor: theme.surface, borderWidth: 1 },
    });
  }
  if (data.trend && xs.length >= 3) {
    const lo = Math.min(...xs);
    const hi = Math.max(...xs);
    series.push({
      name: "Trend line",
      type: "line",
      data: [
        [lo, data.trend.intercept + data.trend.slope * lo],
        [hi, data.trend.intercept + data.trend.slope * hi],
      ],
      showSymbol: false,
      silent: true,
      lineStyle: { type: "dashed", width: 1.5, color: theme.muted },
      tooltip: { show: false },
    });
  }
  const allPositive = data.points.every((p) => p.x > 0 && p.y > 0);
  const log = ctx.logScale && allPositive;
  return {
    color: theme.series,
    animationDuration: 500,
    grid: { left: 22, right: 16, top: 30, bottom: 26, containLabel: true },
    legend: legend(theme, series.map((s) => String(s.name))),
    tooltip: {
      ...tooltipBase(theme),
      trigger: "item",
      formatter: (p: unknown) => {
        const item = p as { value: [number, number, number, string]; marker: string; seriesName: string };
        if (item.seriesName === "Trend line") return "";
        const [x, y, size, name] = item.value;
        const rows = [
          `<div style="font-weight:600;margin-bottom:4px">${item.marker}${name}${item.seriesName === "Unusual" ? " (unusual)" : ""}</div>`,
          `${humanize(data.xName)}: <b>${formatValue(x, fx)}</b>`,
          `${humanize(data.yName)}: <b>${formatValue(y, fy)}</b>`,
        ];
        if (data.sizeName) rows.push(`${humanize(data.sizeName)}: <b>${formatValue(size, fs)}</b>`);
        return rows.join("<br/>");
      },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: 0, zoomOnMouseWheel: "shift" },
      { type: "inside", yAxisIndex: 0, zoomOnMouseWheel: "shift" },
    ],
    xAxis: valueAxis(theme, fx, {
      type: log ? "log" : "value",
      scale: true,
      name: measureTitle(data.xName, fx),
      nameLocation: "middle",
      nameGap: 26,
      splitLine: { lineStyle: { color: theme.grid } },
    }),
    yAxis: valueAxis(theme, fy, { type: log ? "log" : "value", scale: true, name: measureTitle(data.yName, fy), nameLocation: "middle", nameRotate: 90, nameGap: 44 }),
    series,
  } as EChartsOption;
}

function histogramOption(plan: SmartPlan, data: Extract<Prepared, { type: "histogram" }>, ctx: OptionContext): EChartsOption {
  const { theme } = ctx;
  const fmt = formatOf(plan.profile.columns.find((c) => c.name === data.measure));
  const labels = data.bins.map((b) => `${axisLabel(b.from, fmt)}–${axisLabel(b.to, fmt)}`);
  return {
    color: theme.series,
    animationDuration: 500,
    grid: { left: 4, right: 12, top: 24, bottom: data.bins.length > 12 && !ctx.compact ? 30 : 4, containLabel: true },
    tooltip: {
      ...tooltipBase(theme),
      trigger: "axis",
      axisPointer: { type: "shadow", shadowStyle: { color: `${theme.grid}88` } },
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number }[])[0];
        const bin = p ? data.bins[p.dataIndex] : undefined;
        if (!bin) return "";
        return `${humanize(data.measure)}: <b>${formatValue(bin.from, fmt)} to ${formatValue(bin.to, fmt)}</b><br/>Rows: <b>${bin.count.toLocaleString("en-US")}</b>`;
      },
    },
    dataZoom: zoom(theme, "x", data.bins.length, ctx.compact),
    xAxis: categoryAxis(theme, labels, (v) => v, { name: measureTitle(data.measure, fmt), nameLocation: "middle", nameGap: 26, nameTextStyle: { color: theme.muted, fontSize: 11 } }),
    yAxis: valueAxis(theme, { unit: "count", fraction: false }, { name: "Rows", nameLocation: "end", minInterval: 1 }),
    series: [
      {
        name: "Rows",
        type: "bar",
        barCategoryGap: "4%",
        data: data.bins.map((b) => b.count),
        itemStyle: { color: theme.series[0], borderRadius: [3, 3, 0, 0] },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: theme.highlight, type: "dashed" },
          label: { color: theme.muted, fontSize: 10, formatter: `Median ${formatValue(data.median, fmt)}` },
          data: [{ xAxis: Math.max(0, data.bins.findIndex((b) => data.median >= b.from && data.median < b.to)) }],
        },
      },
    ],
  } as EChartsOption;
}

export function buildOption(plan: SmartPlan, data: Prepared, ctx: OptionContext): EChartsOption | null {
  if (data.type === "category") return categoryOption(plan, data, ctx);
  if (data.type === "points") return pointOption(plan, data, ctx);
  if (data.type === "histogram") return histogramOption(plan, data, ctx);
  return null;
}

/** Log scale only helps lines/points whose values span 1000x or more. */
export function canUseLog(plan: SmartPlan, data: Prepared): boolean {
  if (plan.kind !== "line" && plan.kind !== "scatter" && plan.kind !== "bubble") return false;
  let values: number[] = [];
  if (data.type === "category") values = data.series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  if (data.type === "points") values = data.points.map((p) => p.y);
  if (!values.length || values.some((v) => v <= 0)) return false;
  return Math.max(...values) / Math.min(...values) >= 1000;
}
