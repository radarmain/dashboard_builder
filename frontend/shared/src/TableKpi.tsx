import { useMemo, useState } from "react";
import { columnIsNumeric, formatDate, formatValue, toNumber, toTime } from "./format";
import { T } from "./tokens";
import type { ElementData } from "./spec";

const empty = <div style={{ padding: 12, fontSize: 14, color: T.onSurfaceVariant }}>No data</div>;

export function TableView({ data, format }: { data: ElementData; format: Record<string, string> }) {
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
