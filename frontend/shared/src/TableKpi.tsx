import { useMemo, useState } from "react";
import { columnIsNumeric, formatDate, formatValue, toNumber, toTime } from "./format";
import type { ElementData } from "./spec";

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

  if (!data.columns.length) return <div className="p-3 text-sm text-slate-400">No data</div>;
  return (
    <div className="h-full overflow-auto">
      <table className="min-w-full text-sm">
        <thead className="sticky top-0 bg-slate-100">
          <tr>
            {data.columns.map((c, i) => (
              <th key={c} onClick={() => setSort((s) => (s?.idx === i ? { idx: i, dir: (s.dir * -1) as 1 | -1 } : { idx: i, dir: 1 }))}
                className={`cursor-pointer select-none whitespace-nowrap px-3 py-1.5 font-medium text-slate-600 ${numeric[i] ? "text-right" : "text-left"}`}>
                {c}{sort?.idx === i ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 1000).map((r, ri) => (
            <tr key={ri} className="border-t border-slate-100 hover:bg-slate-50">
              {r.map((v, ci) => {
                const hint = format[data.columns[ci]];
                const t = !numeric[ci] && typeof v === "string" ? toTime(v) : null;
                const text = t !== null && v !== null && String(v).length > 10 ? formatDate(t, true) : t !== null ? formatDate(t) : formatValue(v, numeric[ci] ? hint ?? "number" : undefined);
                return <td key={ci} className={`whitespace-nowrap px-3 py-1 ${numeric[ci] ? "text-right tabular-nums" : ""}`}>{text}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 1000 && <div className="p-2 text-xs text-slate-400">Showing first 1,000 of {rows.length} rows</div>}
    </div>
  );
}

/** KPI: the first row; each column becomes one big number with its label. */
export function KpiView({ data, format }: { data: ElementData; format: Record<string, string> }) {
  const row = data.rows[0];
  if (!row) return <div className="p-3 text-sm text-slate-400">No data</div>;
  return (
    <div className="flex h-full flex-wrap items-center justify-around gap-4 p-3">
      {data.columns.slice(0, 6).map((c, i) => (
        <div key={c} className="text-center">
          <div className="text-3xl font-semibold tabular-nums text-slate-900">
            {formatValue(row[i], format[c] ?? (toNumber(row[i]) !== null ? "number" : undefined))}
          </div>
          <div className="mt-1 text-xs uppercase tracking-wide text-slate-500">{c.replace(/_/g, " ")}</div>
        </div>
      ))}
    </div>
  );
}
