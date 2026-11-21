import { useMemo } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { columnIsDate, columnIsNumeric, formatDate, formatValue, toNumber, toTime } from "./format";
import type { ChartDef, ElementData } from "./spec";

import { CHART_COLORS as COLORS, T } from "./tokens";

const AXIS = { fontSize: 11, fontFamily: T.font, fill: T.onSurfaceVariant };
const TIP_BOX = {
  borderRadius: 12, border: `1px solid ${T.outlineVariant}`, background: T.surface, color: T.onSurface,
  fontFamily: T.font, fontSize: 12, boxShadow: "0 2px 8px rgba(0,0,0,0.12)", padding: "8px 12px",
};

type XKind = "time" | "number" | "category";
interface Prepared {
  rows: Record<string, string | number | null>[];
  keys: string[]; // value series keys
  xKind: XKind;
}

/** Coerce raw rows (decimals/dates arrive as strings) into chart-ready objects. */
export function prepare(chart: ChartDef, data: ElementData): Prepared | { error: string } {
  const col = (name: string) => data.columns.indexOf(name);
  const xi = col(chart.x);
  if (xi < 0) return { error: `x column "${chart.x}" is not in the result` };
  const yIdx = chart.y.map(col);
  const missing = chart.y.filter((_, i) => yIdx[i] < 0);
  if (!chart.y.length) return { error: "pick at least one y column" };
  if (missing.length) return { error: `y column(s) not in result: ${missing.join(", ")}` };
  const si = chart.series ? col(chart.series) : -1;

  let xKind: XKind = "category";
  if (chart.kind !== "bar" && chart.kind !== "pie") {
    if (columnIsDate(data.rows, xi) && !columnIsNumeric(data.rows, xi)) xKind = "time";
    else if (columnIsNumeric(data.rows, xi)) xKind = "number";
  } else if (columnIsDate(data.rows, xi)) {
    xKind = "time"; // bars keep a category axis but sort chronologically + label as dates
  }
  const xVal = (v: unknown): string | number | null => {
    if (xKind === "time") return toTime(v);
    if (xKind === "number") return toNumber(v);
    return v === null || v === undefined ? "(blank)" : String(v);
  };

  const keys: string[] = [];
  const byX = new Map<string | number, Record<string, string | number | null>>();
  const out: Record<string, string | number | null>[] = [];
  for (const r of data.rows) {
    const x = xVal(r[xi]);
    if (x === null) continue;
    if (si >= 0) {
      // pivot: one series per distinct value of the series column, using the first y
      const s = r[si] === null || r[si] === undefined ? "(blank)" : String(r[si]);
      if (!keys.includes(s)) keys.push(s);
      const row = byX.get(x) ?? { x };
      row[s] = (row[s] as number | undefined ?? 0) + (toNumber(r[yIdx[0]]) ?? 0);
      if (!byX.has(x)) { byX.set(x, row); out.push(row); }
    } else {
      const row: Record<string, string | number | null> = { x };
      chart.y.forEach((y, i) => { row[y] = toNumber(r[yIdx[i]]); });
      out.push(row);
    }
  }
  if (si < 0) keys.push(...chart.y);
  if (xKind === "time" || xKind === "number") out.sort((a, b) => (a.x as number) - (b.x as number));
  return { rows: out.slice(0, 5000), keys, xKind };
}

export function ChartView({ chart, data }: { chart: ChartDef; data: ElementData }) {
  const prep = useMemo(() => prepare(chart, data), [chart, data]);
  if ("error" in prep) return <Notice text={prep.error} />;
  if (!prep.rows.length) return <Notice text="No data" />;
  const { rows, keys, xKind } = prep;
  const fmtY = (key: string) => (v: unknown) => formatValue(v, chart.format?.[key] ?? chart.format?.[chart.y[0]]);
  const fmtX = (v: unknown) => (xKind === "time" ? formatDate(Number(v)) : String(v));
  const tipFmt = (v: unknown, name: string) => [fmtY(name)(v), name] as [string, string];
  const timeAxis = xKind === "time" && chart.kind !== "bar";
  const xAxis = (
    <XAxis
      dataKey="x"
      tickFormatter={fmtX}
      {...(timeAxis || (xKind === "number" && chart.kind !== "bar")
        ? { type: "number" as const, domain: ["dataMin", "dataMax"] as [string, string], scale: timeAxis ? ("time" as const) : ("auto" as const) }
        : { interval: "preserveStartEnd" as const })}
      tick={AXIS} stroke={T.outline}
    />
  );
  const yAxis = <YAxis tick={AXIS} stroke={T.outline} tickFormatter={fmtY(keys[0])} width={70} />;
  const common = { data: rows, margin: { top: 8, right: 16, bottom: 4, left: 0 } };
  const tip = <Tooltip labelFormatter={fmtX} formatter={tipFmt as never} contentStyle={TIP_BOX} itemStyle={{ color: T.onSurface }} cursor={{ fill: T.surfaceVariant, fillOpacity: 0.5 }} />;
  const legend = keys.length > 1 ? <Legend wrapperStyle={{ fontSize: 12, fontFamily: T.font, color: T.onSurfaceVariant }} /> : null;
  const grid = <CartesianGrid strokeDasharray="3 3" stroke={T.outlineVariant} vertical={false} />;

  let body: React.ReactElement;
  switch (chart.kind) {
    case "line":
      body = <LineChart {...common}>{grid}{xAxis}{yAxis}{tip}{legend}
        {keys.map((k, i) => <Line key={k} dataKey={k} stroke={COLORS[i % COLORS.length]} dot={rows.length < 40} strokeWidth={2} connectNulls />)}</LineChart>;
      break;
    case "area":
      body = <AreaChart {...common}>{grid}{xAxis}{yAxis}{tip}{legend}
        {keys.map((k, i) => <Area key={k} dataKey={k} stackId={chart.stacked ? "s" : undefined} stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.3} connectNulls />)}</AreaChart>;
      break;
    case "scatter":
      body = <ScatterChart margin={common.margin}>{grid}
        <XAxis dataKey="x" type="number" domain={["dataMin", "dataMax"]} tickFormatter={fmtX} tick={AXIS} stroke={T.outline} />
        <YAxis dataKey="y" type="number" tick={AXIS} stroke={T.outline} width={70} />{tip}{legend}
        {keys.map((k, i) => (
          <Scatter key={k} name={k} fill={COLORS[i % COLORS.length]}
            data={rows.map((r) => ({ x: r.x, y: r[k] })).filter((p) => p.y !== null)} />
        ))}</ScatterChart>;
      break;
    case "pie": {
      const k = keys[0];
      const slices = rows.map((r) => ({ name: String(xKind === "time" ? formatDate(Number(r.x)) : r.x), value: Math.max(0, Number(r[k] ?? 0)) }));
      body = <PieChart><Tooltip formatter={tipFmt as never} contentStyle={TIP_BOX} itemStyle={{ color: T.onSurface }} /><Legend wrapperStyle={{ fontSize: 12, fontFamily: T.font, color: T.onSurfaceVariant }} />
        <Pie data={slices} dataKey="value" nameKey="name" outerRadius="75%" label={slices.length <= 8}>
          {slices.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}</Pie></PieChart>;
      break;
    }
    default:
      body = <BarChart {...common}>{grid}{xAxis}{yAxis}{tip}{legend}
        {keys.map((k, i) => <Bar key={k} dataKey={k} stackId={chart.stacked ? "s" : undefined} fill={COLORS[i % COLORS.length]} />)}</BarChart>;
  }
  return <ResponsiveContainer width="100%" height="100%">{body}</ResponsiveContainer>;
}

export function Notice({ text, tone = "muted" }: { text: string; tone?: "muted" | "error" }) {
  return (
    <div style={{ display: "flex", height: "100%", alignItems: "center", justifyContent: "center", padding: 12, textAlign: "center", fontSize: 14, fontFamily: T.font, color: tone === "error" ? T.error : T.onSurfaceVariant }}>
      {text}
    </div>
  );
}
