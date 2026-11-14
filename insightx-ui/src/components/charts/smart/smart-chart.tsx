"use client";

// SmartChart: one chart component for every query result. It profiles the
// columns, picks the right chart (or the one the user switches to), checks
// it is honest before drawing, and draws it with Apache ECharts with the
// interactive tools a BI chart needs.

import * as React from "react";
import type { EChartsType } from "echarts/core";
import {
  AreaChart as AreaIcon,
  ArrowDownWideNarrow,
  BarChart3,
  BarChartHorizontal,
  ChartColumnStacked,
  ChartNoAxesCombined,
  ChartScatter,
  ChevronRight,
  Circle,
  Download,
  Ellipsis,
  FileImage,
  FileSpreadsheet,
  Info,
  Layers,
  LineChart as LineIcon,
  Maximize2,
  Minimize2,
  PieChart as PieIcon,
  RotateCcw,
  Sparkles,
  Table2,
  X,
} from "lucide-react";
import { DataTable, downloadCsv } from "@/components/common/data-table";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Hint } from "@/components/ui/tooltip";
import { chartInsights, chartTitle } from "@/lib/chart-intel/insights";
import { formatOf, formatValue } from "@/lib/chart-intel/format";
import { buildOption, canUseLog } from "@/lib/chart-intel/options";
import { DEFAULT_VIEW, prepareData, type SortMode, type ViewState } from "@/lib/chart-intel/prepare";
import { isDateValue } from "@/lib/chart-intel/profile";
import { KIND_LABELS, recommendChart, validatePlan, withKind, type SmartKind, type SmartPlan } from "@/lib/chart-intel/recommend";
import { humanize } from "@/lib/format";
import type { Row } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useChartLink } from "@/components/charts/smart/chart-links";
import { useChartTheme } from "@/components/charts/smart/chart-theme";

type EChartsModule = typeof import("@/components/charts/smart/echarts-core");

const KIND_ICONS: Record<SmartKind, typeof BarChart3> = {
  line: LineIcon,
  bar: BarChart3,
  "stacked-bar": ChartColumnStacked,
  area: AreaIcon,
  "stacked-area": Layers,
  donut: PieIcon,
  scatter: ChartScatter,
  bubble: Circle,
  combo: ChartNoAxesCombined,
  histogram: BarChartHorizontal,
  metric: BarChart3,
};

const RANGES = [
  { key: "all", label: "All", days: 0 },
  { key: "30d", label: "30D", days: 30 },
  { key: "90d", label: "90D", days: 90 },
  { key: "6m", label: "6M", days: 182 },
  { key: "1y", label: "1Y", days: 365 },
  { key: "ytd", label: "YTD", days: -1 },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

const SORTS: { key: SortMode; label: string }[] = [
  { key: "natural", label: "As returned" },
  { key: "value-desc", label: "Highest first" },
  { key: "value-asc", label: "Lowest first" },
  { key: "label", label: "A to Z" },
];

let echartsPromise: Promise<EChartsModule> | null = null;
function loadECharts(): Promise<EChartsModule> {
  echartsPromise ??= import("@/components/charts/smart/echarts-core");
  return echartsPromise;
}

function toTime(value: unknown): number | null {
  if (typeof value === "number") return value >= 1900 && value <= 2100 ? new Date(value, 0, 1).getTime() : null;
  if (!isDateValue(value)) return null;
  const text = String(value).trim();
  const date = new Date(text.length === 7 ? `${text}-01` : text);
  return Number.isNaN(date.getTime()) ? null : date.getTime();
}

function fileName(title: string, ext: string): string {
  const base = title.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-").toLowerCase().slice(0, 60) || "insightx-chart";
  return `${base}.${ext}`;
}

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** First chart type in the plan's list that passes the honesty checks. */
function firstValid(plan: SmartPlan, rows: Row[]): { plan: SmartPlan; problems: string[] } | null {
  const tried = validatePlan(plan, rows);
  if (tried.ok) return { plan, problems: [] };
  for (const kind of plan.options) {
    if (kind === plan.kind) continue;
    const next = withKind(plan, kind);
    if (validatePlan(next, rows).ok) return { plan: next, problems: tried.problems };
  }
  return null;
}

function Metrics({ plan, row }: { plan: SmartPlan; row: Row }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {plan.ys.map((column) => (
        <div key={column} className="rounded-lg border bg-surface px-4 py-3">
          <p className="text-xs text-muted-foreground">{humanize(column)}</p>
          <p className="tabular mt-1 text-2xl font-semibold tracking-tight">
            {formatValue(row[column], formatOf(plan.profile.columns.find((c) => c.name === column)), 1e7)}
          </p>
        </div>
      ))}
    </div>
  );
}

function KindSwitch({ plan, onChange }: { plan: SmartPlan; onChange: (kind: SmartKind) => void }) {
  if (plan.options.length < 2) return null;
  return (
    <div role="radiogroup" aria-label="Chart type" className="inline-flex flex-wrap rounded-md border p-0.5">
      {plan.options.map((kind) => {
        const Icon = KIND_ICONS[kind];
        const active = plan.kind === kind;
        return (
          <Hint key={kind} label={KIND_LABELS[kind]}>
            <button
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={`${KIND_LABELS[kind]} chart`}
              onClick={() => onChange(kind)}
              className={cn(
                "flex size-6 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground",
                active && "bg-accent text-foreground",
              )}
            >
              <Icon className="size-3.5" />
            </button>
          </Hint>
        );
      })}
    </div>
  );
}

function Chip({ children, onClear }: { children: React.ReactNode; onClear?: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border bg-accent/50 py-0.5 pr-1 pl-2.5 text-[11px] text-foreground">
      {children}
      {onClear && (
        <button type="button" onClick={onClear} aria-label="Clear filter" className="rounded-full p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground">
          <X className="size-3" />
        </button>
      )}
    </span>
  );
}

export interface SmartChartProps {
  columns: string[];
  rows: Row[];
  /** Chart type hint from the backend ("line", "pie", ...). */
  preferred?: string;
  /** The user's question, used to read intent (trend, share, compare...). */
  question?: string;
  /** Override the automatic title. */
  title?: string;
  showTitle?: boolean;
  height?: number;
  /** A category value to draw in the accent colour. */
  highlight?: string;
  className?: string;
  showSwitch?: boolean;
  showInsights?: boolean;
}

export function SmartChart({
  columns,
  rows,
  preferred,
  question,
  title,
  showTitle = true,
  height = 280,
  highlight,
  className,
  showSwitch = true,
  showInsights = true,
}: SmartChartProps) {
  const id = React.useId();
  const theme = useChartTheme();
  const link = useChartLink();
  const [kind, setKind] = React.useState<SmartKind | null>(null);
  const [view, setView] = React.useState<ViewState>(DEFAULT_VIEW);
  const [drill, setDrill] = React.useState<{ column: string; value: string }[]>([]);
  const [range, setRange] = React.useState<RangeKey>("all");
  const [showData, setShowData] = React.useState(false);
  const [full, setFull] = React.useState(false);
  const [compact, setCompact] = React.useState(false);

  const boxRef = React.useRef<HTMLDivElement>(null);
  const chartRef = React.useRef<EChartsType | null>(null);
  const moduleRef = React.useRef<EChartsModule | null>(null);
  const [ready, setReady] = React.useState(false);

  // 1. cross-filter from a sibling chart (only if this result has that column)
  const cross = link.filter && link.filter.source !== id && columns.includes(link.filter.column) ? link.filter : null;
  const selected = link.filter && link.filter.source === id ? link.filter.value : (highlight ?? null);

  const working = React.useMemo(() => {
    let out = rows;
    if (cross) out = out.filter((row) => String(row[cross.column] ?? "(empty)") === cross.value);
    for (const step of drill) out = out.filter((row) => String(row[step.column] ?? "(empty)") === step.value);
    return out;
  }, [rows, cross, drill]);

  const drilledColumns = React.useMemo(() => new Set(drill.map((d) => d.column)), [drill]);
  const activeColumns = React.useMemo(
    () => (columns.length ? columns : Object.keys(rows[0] ?? {})).filter((c) => !drilledColumns.has(c)),
    [columns, rows, drilledColumns],
  );

  // 2. pick the chart before the date range is applied, so the type stays stable
  const basePlan = React.useMemo(
    () => (working.length ? recommendChart(activeColumns, working, { preferred, question }) : null),
    [activeColumns, working, preferred, question],
  );

  // 3. date range presets for time charts
  const timeSpan = React.useMemo(() => {
    if (!basePlan || basePlan.xRole !== "temporal") return null;
    const times = working.map((row) => toTime(row[basePlan.x])).filter((t): t is number => t !== null);
    if (times.length < 3) return null;
    return { min: Math.min(...times), max: Math.max(...times) };
  }, [basePlan, working]);
  const ranged = React.useMemo(() => {
    if (!basePlan || !timeSpan || range === "all") return working;
    const preset = RANGES.find((r) => r.key === range);
    if (!preset) return working;
    const end = new Date(timeSpan.max);
    const from = preset.days === -1 ? new Date(end.getFullYear(), 0, 1).getTime() : timeSpan.max - preset.days * 86400000;
    return working.filter((row) => {
      const t = toTime(row[basePlan.x]);
      return t === null || t >= from;
    });
  }, [basePlan, timeSpan, range, working]);

  const checked = React.useMemo(() => {
    if (!basePlan || !ranged.length) return null;
    const plan = kind && basePlan.options.includes(kind) ? withKind(basePlan, kind) : basePlan;
    return firstValid(plan, ranged);
  }, [basePlan, kind, ranged]);
  const plan = checked?.plan ?? null;

  const prepared = React.useMemo(() => (plan ? prepareData(plan, ranged, view) : null), [plan, ranged, view]);
  const option = React.useMemo(
    () => (plan && prepared ? buildOption(plan, prepared, { theme, logScale: view.logScale, selected, compact: compact && !full }) : null),
    [plan, prepared, theme, view.logScale, selected, compact, full],
  );
  const autoTitle = plan ? (title ?? chartTitle(plan)) : (title ?? "");
  const insights = React.useMemo(() => (plan && prepared && showInsights ? chartInsights(plan, prepared) : []), [plan, prepared, showInsights]);
  const notes = [...(checked?.problems.length ? [`Switched chart: ${checked.problems[0]}`] : []), ...(prepared?.notes ?? [])];

  // drill-down is possible when another category is left to break into
  const canDrill = !!plan && plan.xRole === "category" && plan.profile.dimensions.filter((d) => d.name !== plan.x).length > 0;

  // ---- ECharts lifecycle ----
  const isChart = !!option;
  React.useEffect(() => {
    if (!isChart) return;
    let disposed = false;
    let observer: ResizeObserver | null = null;
    void loadECharts().then((mod) => {
      if (disposed || !boxRef.current) return;
      moduleRef.current = mod;
      const chart = mod.echarts.init(boxRef.current, undefined, { renderer: "canvas" });
      chartRef.current = chart;
      observer = new ResizeObserver((entries) => {
        chart.resize();
        const width = entries[0]?.contentRect.width ?? 999;
        setCompact(width < 420);
      });
      observer.observe(boxRef.current);
      setReady(true);
    });
    return () => {
      disposed = true;
      observer?.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
      setReady(false);
    };
  }, [isChart]);

  React.useEffect(() => {
    if (ready && chartRef.current && option) chartRef.current.setOption(option, { notMerge: true });
  }, [ready, option]);

  // click: drill down when possible, otherwise filter (and cross-filter) by the clicked group
  const onClick = React.useRef<(params: { name?: string; componentType?: string; seriesType?: string }) => void>(() => undefined);
  onClick.current = (params) => {
    if (!plan || params.componentType !== "series" || !params.name) return;
    if (plan.xRole !== "category" || plan.kind === "scatter" || plan.kind === "bubble" || plan.kind === "histogram") return;
    if (params.name === "Other" || params.name === "(empty)") return;
    if (canDrill) {
      setDrill((steps) => [...steps, { column: plan.x, value: params.name as string }]);
      setKind(null);
      return;
    }
    const same = link.filter?.source === id && link.filter.value === params.name;
    link.setFilter(same ? null : { column: plan.x, value: params.name, source: id });
  };
  const gridClick = React.useRef<{ categories: string[]; horizontal: boolean } | null>(null);
  gridClick.current =
    prepared?.type === "category" && plan && plan.kind !== "donut"
      ? { categories: prepared.categories, horizontal: (plan.kind === "bar" || plan.kind === "stacked-bar") && plan.horizontal && plan.xRole !== "temporal" }
      : null;
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!ready || !chart) return;
    // Donut slices: the slice itself is the target.
    const handler = (params: unknown) => {
      const p = params as { name?: string; componentType?: string; seriesType?: string };
      if (p.seriesType === "pie") onClick.current(p);
    };
    // Bars and lines: a click anywhere in a column picks that group, so thin bars are easy to hit.
    const zr = chart.getZr();
    const zrHandler = (event: { offsetX: number; offsetY: number }) => {
      const info = gridClick.current;
      if (!info || !chart.containPixel("grid", [event.offsetX, event.offsetY])) return;
      const point = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[];
      const index = Math.round((info.horizontal ? point[1] : point[0]) ?? -1);
      const name = info.categories[index];
      if (name !== undefined) onClick.current({ name, componentType: "series" });
    };
    chart.on("click", handler);
    zr.on("click", zrHandler);
    return () => {
      chart.off("click", handler);
      zr.off("click", zrHandler);
    };
  }, [ready]);

  React.useEffect(() => {
    if (!full) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setFull(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);
  React.useEffect(() => {
    const timer = setTimeout(() => chartRef.current?.resize(), 30);
    return () => clearTimeout(timer);
  }, [full, showData]);

  // ---- exports ----
  const exportPng = () => {
    const url = chartRef.current?.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: theme.surface });
    if (!url) return;
    const link2 = document.createElement("a");
    link2.href = url;
    link2.download = fileName(autoTitle, "png");
    link2.click();
  };
  const exportSvg = async () => {
    if (!option) return;
    const mod = moduleRef.current ?? (await loadECharts());
    const width = boxRef.current?.clientWidth || 900;
    const h = boxRef.current?.clientHeight || height;
    const svgChart = mod.echarts.init(null, undefined, { renderer: "svg", ssr: true, width, height: h });
    svgChart.setOption({ ...option, animation: false, backgroundColor: theme.surface, dataZoom: [] });
    const svg = svgChart.renderToSVGString();
    svgChart.dispose();
    downloadBlob(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }), fileName(autoTitle, "svg"));
  };
  const exportCsv = () => {
    if (!prepared) return;
    downloadCsv(prepared.table.columns, prepared.table.rows, fileName(autoTitle, "csv"));
  };
  const resetZoom = () => chartRef.current?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
  const resetAll = () => {
    setKind(null);
    setView(DEFAULT_VIEW);
    setDrill([]);
    setRange("all");
    if (link.filter?.source === id) link.setFilter(null);
    resetZoom();
  };

  if (!rows.length) return null;
  if (!plan || !prepared) {
    return (
      <p className={cn("rounded-lg border border-dashed px-4 py-6 text-center text-xs text-muted-foreground", className)}>
        {cross ? "No rows match the current filter." : "This result has no column pair that makes an honest chart. See the table instead."}
      </p>
    );
  }
  if (plan.kind === "metric") return <Metrics plan={plan} row={ranged[0] as Row} />;

  const categoryChart = plan.xRole === "category" && plan.kind !== "scatter" && plan.kind !== "bubble" && plan.kind !== "histogram";
  const logOk = canUseLog(plan, prepared);
  const chartHeight = full ? "calc(100vh - 220px)" : plan.kind === "bar" && plan.horizontal && prepared.type === "category"
    ? Math.max(height, Math.min(prepared.categories.length, 20) * 24 + 40)
    : height;
  const showRanges = !!timeSpan && (plan.kind === "line" || plan.kind === "area" || plan.kind === "bar" || plan.kind === "stacked-area" || plan.kind === "stacked-bar" || plan.kind === "combo");
  const spanDays = timeSpan ? (timeSpan.max - timeSpan.min) / 86400000 : 0;

  return (
    <div
      className={cn(
        "min-w-0 space-y-2",
        full && "fixed inset-0 z-50 flex flex-col gap-2 space-y-0 overflow-y-auto bg-background p-6",
        className,
      )}
      role={full ? "dialog" : undefined}
      aria-modal={full || undefined}
      aria-label={full ? autoTitle : undefined}
    >
      {/* toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          {showTitle && (
            <Hint label={plan.reason}>
              <h4 className={cn("truncate font-medium", full ? "text-lg" : "text-[13px]")}>{autoTitle}</h4>
            </Hint>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1">
          {showSwitch && <KindSwitch plan={plan} onChange={(k) => setKind(k)} />}
          <Hint label={showData ? "Hide data" : "View data"}>
            <Button variant="ghost" size="icon-sm" className={cn("size-7", showData && "bg-accent")} aria-label="View data" onClick={() => setShowData((v) => !v)}>
              <Table2 className="size-3.5" />
            </Button>
          </Hint>
          <Hint label={full ? "Exit full screen" : "Full screen"}>
            <Button variant="ghost" size="icon-sm" className="size-7" aria-label={full ? "Exit full screen" : "Full screen"} onClick={() => setFull((v) => !v)}>
              {full ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            </Button>
          </Hint>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" className="size-7" aria-label="More chart options">
                <Ellipsis className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              {categoryChart && plan.xRole === "category" && (
                <>
                  <DropdownMenuLabel>Sort</DropdownMenuLabel>
                  {SORTS.map((s) => (
                    <DropdownMenuItem key={s.key} onSelect={() => setView((v) => ({ ...v, sort: s.key }))} className={cn(view.sort === s.key && "bg-accent/60")}>
                      <ArrowDownWideNarrow /> {s.label}
                    </DropdownMenuItem>
                  ))}
                  <DropdownMenuSeparator />
                  <DropdownMenuLabel>Show</DropdownMenuLabel>
                  {[5, 10, 20, null].map((n) => (
                    <DropdownMenuItem key={String(n)} onSelect={() => setView((v) => ({ ...v, topN: n }))} className={cn(view.topN === n && "bg-accent/60")}>
                      <BarChart3 /> {n ? `Top ${n}` : "Automatic"}
                    </DropdownMenuItem>
                  ))}
                  <DropdownMenuSeparator />
                </>
              )}
              {logOk && (
                <>
                  <DropdownMenuItem onSelect={() => setView((v) => ({ ...v, logScale: !v.logScale }))}>
                    <ChartNoAxesCombined /> {view.logScale ? "Normal scale" : "Log scale (for very wide ranges)"}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuLabel>Download</DropdownMenuLabel>
              <DropdownMenuItem onSelect={exportPng}>
                <FileImage /> PNG image
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportSvg()}>
                <Download /> SVG image
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={exportCsv}>
                <FileSpreadsheet /> CSV data
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={resetZoom}>
                <RotateCcw /> Reset zoom
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={resetAll}>
                <RotateCcw /> Reset everything
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* filters, drill path, date range */}
      {(drill.length > 0 || cross || (selected && link.filter?.source === id) || showRanges) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {drill.length > 0 && (
            <nav aria-label="Drill path" className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
              <button type="button" className="hover:text-foreground hover:underline" onClick={() => setDrill([])}>
                All
              </button>
              {drill.map((step, index) => (
                <React.Fragment key={`${step.column}-${step.value}`}>
                  <ChevronRight className="size-3" />
                  <button
                    type="button"
                    className={cn("hover:text-foreground hover:underline", index === drill.length - 1 && "font-medium text-foreground")}
                    onClick={() => setDrill((steps) => steps.slice(0, index + 1))}
                  >
                    {humanize(step.column)}: {step.value}
                  </button>
                </React.Fragment>
              ))}
            </nav>
          )}
          {cross && (
            <Chip onClear={() => link.setFilter(null)}>
              Filtered by {humanize(cross.column)} = {cross.value}
            </Chip>
          )}
          {link.filter?.source === id && (
            <Chip onClear={() => link.setFilter(null)}>
              {link.linked ? "Filtering other charts" : "Selected"}: {link.filter.value}
            </Chip>
          )}
          {showRanges && (
            <div role="radiogroup" aria-label="Date range" className="ml-auto inline-flex rounded-md border p-0.5">
              {RANGES.filter((r) => r.key === "all" || r.key === "ytd" || r.days < spanDays).map((r) => (
                <button
                  key={r.key}
                  type="button"
                  role="radio"
                  aria-checked={range === r.key}
                  onClick={() => setRange(r.key)}
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground",
                    range === r.key && "bg-accent text-foreground",
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        ref={boxRef}
        style={{ height: chartHeight }}
        className={cn("w-full", categoryChart && "cursor-pointer")}
        role="img"
        aria-label={`${autoTitle}. ${insights.join(" ")}`}
      />

      {(insights.length > 0 || notes.length > 0) && (
        <div className="space-y-1 text-xs">
          {insights.map((text) => (
            <p key={text} className="flex items-start gap-1.5 text-foreground/85">
              <Sparkles className="mt-0.5 size-3 shrink-0 text-primary" />
              {text}
            </p>
          ))}
          {notes.map((text) => (
            <p key={text} className="flex items-start gap-1.5 text-muted-foreground">
              <Info className="mt-0.5 size-3 shrink-0" />
              {text}
            </p>
          ))}
          {categoryChart && (
            <p className="text-[11px] text-muted-foreground/80">
              {canDrill ? "Click a bar or slice to drill down." : link.linked ? "Click a bar or slice to filter the other charts." : "Click a bar or slice to highlight it."}
            </p>
          )}
        </div>
      )}

      {showData && (
        <DataTable columns={prepared.table.columns} rows={prepared.table.rows} pageSize={full ? 15 : 8} exportName={fileName(autoTitle, "csv")} />
      )}
    </div>
  );
}
