import { useMemo, useState } from "react";
import { columnIsNumeric, formatDate, formatValue, toNumber, toTime } from "./format";
import { HEAT_STOPS, T } from "./tokens";
import type { ElementData, HeatmapDef, PivotDef } from "./spec";

const empty = <div style={{ padding: 12, fontSize: 14, color: T.onSurfaceVariant }}>No data</div>;

export function TableView({ data, format, pivot, heatmap }: { data: ElementData; format: Record<string, string>; pivot?: PivotDef | null; heatmap?: HeatmapDef | null }) {
  if (pivot) return <PivotTable data={data} format={format} pivot={pivot} heatmap={heatmap ?? null} />;
  return <PlainTable data={data} format={format} />;
}

/** value 0..1 -> "rgb(...)" along the palette's three stops. */
function heatColor(t: number, palette: "diverging" | "sequential"): string {
  const s = HEAT_STOPS[palette];
  const x = Math.min(1, Math.max(0, t)) * 2;
  const i = Math.min(1, Math.floor(x));
  const f = x - i;
  const c = s[i].map((a, k) => Math.round(a + (s[i + 1][k] - a) * f));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

function labelFor(v: unknown): string {
  const t = toTime(v);
  if (t !== null) return new Date(t).toLocaleDateString(undefined, { timeZone: "UTC", month: "short", year: "numeric" });
  return v === null || v === undefined ? "(blank)" : String(v);
}

function PivotTable({ data, format, pivot, heatmap }: { data: ElementData; format: Record<string, string>; pivot: PivotDef; heatmap: HeatmapDef | null }) {
  const m = useMemo(() => {
    const ri = data.columns.indexOf(pivot.row), ci = data.columns.indexOf(pivot.column), vi = data.columns.indexOf(pivot.value);
    if (ri < 0 || ci < 0 || vi < 0) return null;
    const colKey = (v: unknown) => String(v ?? "");
    const cols = new Map<string, unknown>();
    const rows = new Map<string, { label: unknown; cells: Map<string, number | null> }>();
    for (const r of data.rows) {
      cols.set(colKey(r[ci]), r[ci]);
      const k = String(r[ri] ?? "");
      const row = rows.get(k) ?? { label: r[ri], cells: new Map() };
      row.cells.set(colKey(r[ci]), toNumber(r[vi]));
      rows.set(k, row);
    }
    const colList = [...cols.entries()].sort((a, b) => (toTime(a[1]) !== null && toTime(b[1]) !== null ? (toTime(a[1]) as number) - (toTime(b[1]) as number) : a[0].localeCompare(b[0])));
    const rowList = [...rows.values()].sort((a, b) => String(a.label ?? "").localeCompare(String(b.label ?? "")));
    return { colList, rowList };
  }, [data, pivot]);
  if (!m) return <div style={{ padding: 12, fontSize: 14, color: T.error }}>Pivot columns are not in the result</div>;
  if (!m.rowList.length) return empty;
  const hint = format[pivot.value] ?? "number";
  const cell = (n: number | null) => {
    if (n === null) return { text: "", style: {} };
    const t = heatmap && heatmap.max !== heatmap.min ? (n - heatmap.min) / (heatmap.max - heatmap.min) : null;
    return { text: formatValue(n, hint), style: t === null ? {} : { background: heatColor(t, heatmap!.palette), color: "#111" } };
  };
  return (
    <div style={{ height: "100%", overflow: "auto", fontFamily: T.font }}>
      <table style={{ minWidth: "100%", fontSize: 13, borderCollapse: "collapse" }}>
        <thead style={{ position: "sticky", top: 0, background: T.surfaceVariant }}>
          <tr>
            <th style={{ padding: "8px 12px", textAlign: "left", fontWeight: 500, color: T.onSurfaceVariant, whiteSpace: "nowrap" }}>{pivot.row.replace(/_/g, " ")}</th>
            {m.colList.map(([k, v]) => <th key={k} style={{ padding: "8px 12px", textAlign: "right", fontWeight: 500, color: T.onSurfaceVariant, whiteSpace: "nowrap" }}>{labelFor(v)}</th>)}
          </tr>
        </thead>
        <tbody>
          {m.rowList.slice(0, 1000).map((r, ri) => (
            <tr key={ri} style={{ borderTop: `1px solid ${T.outlineVariant}` }}>
              <td style={{ padding: "6px 12px", whiteSpace: "nowrap" }}>{labelFor(r.label)}</td>
              {m.colList.map(([k]) => {
                const c = cell(r.cells.get(k) ?? null);
                return <td key={k} style={{ padding: "6px 12px", textAlign: "right", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums", ...c.style }}>{c.text}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlainTable({ data, format }: { data: ElementData; format: Record<string, string> }) {
  const [sort, setSort] = useState<{ idx: number; dir: 1 | -1 } | null>(null);
  const numeric = useMemo(() => data.columns.map((_, i) => columnIsNumeric(data.rows, i)), [data]);
  const rows = useMemo(() => {
    if (!sort) return data.rows;
    const { idx, dir } = sort;
    return [...data.rows].sort((a, b) => {
      const av = numeric[idx] ? toNumber(a[idx]) : toTime(a[idx]) ?? String(a[idx] ?? "");
      const bv = numeric[idx] ? toNumber(b[idx]) : toTime(b[idx]) ?? String(b[idx] ?? "");
      if (av === null || av === "") return 1;
      if (bv === null || bv === "") return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * dir;
    });
  }, [data, sort, numeric]);

  if (!data.columns.length) return empty;
  return (
    <div style={{ height: "100%", overflow: "auto", fontFamily: T.font }}>
      <table style={{ minWidth: "100%", fontSize: 13, borderCollapse: "collapse" }}>
        <thead style={{ position: "sticky", top: 0, background: T.surfaceVariant }}>
          <tr>
            {data.columns.map((c, i) => (
              <th key={c} onClick={() => setSort((s) => (s?.idx === i ? { idx: i, dir: (s.dir * -1) as 1 | -1 } : { idx: i, dir: 1 }))}
                style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap", padding: "8px 12px", fontWeight: 500, color: T.onSurfaceVariant, textAlign: numeric[i] ? "right" : "left" }}>
                {c}{sort?.idx === i ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 1000).map((r, ri) => (
            <tr key={ri} className="rdb-row" style={{ borderTop: `1px solid ${T.outlineVariant}` }}>
              {r.map((v, ci) => {
                const hint = format[data.columns[ci]];
                const t = !numeric[ci] && typeof v === "string" ? toTime(v) : null;
                const text = t !== null && v !== null && String(v).length > 10 ? formatDate(t, true) : t !== null ? formatDate(t) : formatValue(v, numeric[ci] ? hint ?? "number" : undefined);
                return <td key={ci} style={{ whiteSpace: "nowrap", padding: "6px 12px", textAlign: numeric[ci] ? "right" : "left", fontVariantNumeric: numeric[ci] ? "tabular-nums" : undefined }}>{text}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 1000 && <div style={{ padding: 8, fontSize: 12, color: T.onSurfaceVariant }}>Showing first 1,000 of {rows.length} rows</div>}
    </div>
  );
}

/** KPI: the first row; each column becomes one big number with its label. */
export function KpiView({ data, format }: { data: ElementData; format: Record<string, string> }) {
  const row = data.rows[0];
  if (!row) return empty;
  return (
    <div style={{ display: "flex", height: "100%", flexWrap: "wrap", alignItems: "center", justifyContent: "space-around", gap: 16, padding: 12, fontFamily: T.font }}>
      {data.columns.slice(0, 6).map((c, i) => (
        <div key={c} style={{ textAlign: "center" }}>
          <div style={{ fontSize: 36, lineHeight: "44px", fontWeight: 400, fontVariantNumeric: "tabular-nums", color: T.primary }}>
            {formatValue(row[i], format[c] ?? (toNumber(row[i]) !== null ? "number" : undefined))}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, lineHeight: "16px", fontWeight: 500, letterSpacing: 0.5, color: T.onSurfaceVariant }}>{c.replace(/_/g, " ")}</div>
        </div>
      ))}
    </div>
  );
}
