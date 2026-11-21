import { useMemo, useState } from "react";
import { SpecRenderer, columnIsNumeric, type ChartDef, type ChartKind, type ElementData, type LayoutItem, type ViewElement, type ViewSpec } from "@rdb/shared";
import { post } from "../api";
import { Btn, ErrorBox, Field, errMsg, inputCls } from "../ui";

const KINDS: ChartKind[] = ["bar", "line", "area", "pie", "scatter"];
const FORMATS = ["", "number", "currency", "percent"];

interface QueryResult extends ElementData { bytes_processed?: number; cache_hit?: boolean; truncated?: boolean }

export function ElementPanel({ element, layout, queryPath, initialData, onApply, onLayout, onRemove, onClose }: {
  element: ViewElement;
  layout: LayoutItem | null;
  queryPath: string;
  initialData: ElementData | null;
  onApply: (el: ViewElement, result?: ElementData) => void;
  onLayout: (patch: Partial<{ x: number; y: number; w: number; h: number }>) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const [el, setEl] = useState<ViewElement>(element);
  const [result, setResult] = useState<QueryResult | null>(initialData);
  const [running, setRunning] = useState(false);
  const [refresh, setRefresh] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);

  const columns = result?.columns ?? [];
  const patch = (p: Partial<ViewElement>) => { setEl((e) => ({ ...e, ...p })); setApplied(false); };
  const patchChart = (p: Partial<ChartDef>) => patch({ chart: { ...(el.chart as ChartDef), ...p } });

  async function run() {
    setError(null);
    if (!el.sql.trim()) { setError("Enter some SQL first."); return; }
    setRunning(true);
    try {
      const r = await post<QueryResult>(queryPath, { sql: el.sql, refresh });
      setResult(r);
      setApplied(false);
      if (el.type === "chart" && el.chart && (!el.chart.x || !columns.length)) {
        // sensible defaults on first run: x = first column, y = first numeric column that isn't x
        const numeric = r.columns.filter((_, i) => columnIsNumeric(r.rows, i));
        const y = numeric.find((c) => c !== r.columns[0]) ?? numeric[0] ?? r.columns[1] ?? r.columns[0];
        const keep = el.chart.x && r.columns.includes(el.chart.x);
        patch({ chart: { ...el.chart, x: keep ? el.chart.x : r.columns[0], y: el.chart.y.filter((c) => r.columns.includes(c)).length ? el.chart.y.filter((c) => r.columns.includes(c)) : [y] } });
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setRunning(false);
    }
  }

  function apply() {
    onApply(el, result ?? undefined);
    setApplied(true);
  }

  const fmtCols = el.type === "chart" ? el.chart?.y ?? [] : columns;
  const fmtGet = (c: string) => (el.type === "chart" ? el.chart?.format?.[c] : el.format?.[c]) ?? "";
  const fmtSet = (c: string, v: string) => {
    const strip = (m: Record<string, string>) => { const n = { ...m }; if (v) n[c] = v; else delete n[c]; return n; };
    if (el.type === "chart") patchChart({ format: strip(el.chart?.format ?? {}) });
    else patch({ format: strip(el.format ?? {}) });
  };

  const previewSpec: ViewSpec = useMemo(() => ({
    schema_version: 1, view: { name: "", slug: "" }, revision: 0, updated_at: "", updated_by: "",
    layout: [{ element_id: el.id, x: 0, y: 0, w: 12, h: 5 }], elements: [el],
  }), [el]);

  return (
    <aside className="flex h-full w-[28rem] shrink-0 flex-col overflow-auto border-l border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div className="font-medium capitalize">{el.type} <span className="font-mono text-xs text-slate-400">{el.id}</span></div>
        <Btn variant="ghost" onClick={onClose}>Close</Btn>
      </div>

      <div className="space-y-4 p-4">
        <Field label="Title"><input className={inputCls} value={el.title} onChange={(e) => patch({ title: e.target.value })} /></Field>

        <Field label="SQL" hint="Only SELECT on this project's connected tables, using fully qualified `project.dataset.table` names.">
          <textarea className={`${inputCls} h-40 font-mono text-xs`} spellCheck={false} value={el.sql} onChange={(e) => patch({ sql: e.target.value })}
            placeholder="SELECT channel, SUM(spend) AS spend FROM `project.dataset.table` GROUP BY channel" />
        </Field>
        <div className="flex items-center gap-3">
          <Btn onClick={run} disabled={running}>{running ? "Running…" : "Run"}</Btn>
          <label className="flex items-center gap-1.5 text-sm text-slate-600">
            <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} /> Refresh (skip cache)
          </label>
        </div>
        <ErrorBox error={error} />

        {result && (
          <div className="space-y-2">
            <div className="text-xs text-slate-500">
              {result.rows.length.toLocaleString()} rows{result.truncated ? " (truncated)" : ""}
              {result.cache_hit !== undefined && ` · ${result.cache_hit ? "cached" : "fresh"}`}
              {result.bytes_processed !== undefined && ` · ${(result.bytes_processed / 1e6).toFixed(1)} MB processed`}
            </div>
            <div className="max-h-44 overflow-auto rounded border border-slate-200">
              <table className="min-w-full text-xs">
                <thead className="sticky top-0 bg-slate-100"><tr>{result.columns.map((c) => <th key={c} className="whitespace-nowrap px-2 py-1 text-left font-medium">{c}</th>)}</tr></thead>
                <tbody>
                  {result.rows.slice(0, 20).map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      {r.map((v, j) => <td key={j} className="max-w-[12rem] truncate whitespace-nowrap px-2 py-1">{v === null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {el.type === "chart" && el.chart && (
          <div className="space-y-3 rounded-md border border-slate-200 p-3">
            <div className="text-sm font-medium">Chart</div>
            <Field label="Kind">
              <select className={inputCls} value={el.chart.kind} onChange={(e) => patchChart({ kind: e.target.value as ChartKind })}>
                {KINDS.map((k) => <option key={k}>{k}</option>)}
              </select>
            </Field>
            <Field label={el.chart.kind === "pie" ? "Label column" : "X column"}>
              <ColSelect cols={columns} value={el.chart.x} onChange={(v) => patchChart({ x: v })} />
            </Field>
            <Field label={el.chart.kind === "pie" ? "Value column" : "Y columns"}>
              {columns.length ? (
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {columns.map((c) => (
                    <label key={c} className="flex items-center gap-1 text-sm">
                      <input type="checkbox" checked={el.chart!.y.includes(c)}
                        onChange={() => patchChart({ y: el.chart!.y.includes(c) ? el.chart!.y.filter((x) => x !== c) : [...el.chart!.y, c] })} /> {c}
                    </label>
                  ))}
                </div>
              ) : <span className="text-xs text-slate-400">Run the query to choose columns{el.chart.y.length ? ` (current: ${el.chart.y.join(", ")})` : ""}</span>}
            </Field>
            {el.chart.kind !== "pie" && (
              <Field label="Series (split by)">
                <ColSelect cols={columns} value={el.chart.series ?? ""} allowNone onChange={(v) => patchChart({ series: v || null })} />
              </Field>
            )}
            {(el.chart.kind === "bar" || el.chart.kind === "area") && (
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={el.chart.stacked} onChange={(e) => patchChart({ stacked: e.target.checked })} /> Stacked</label>
            )}
          </div>
        )}

        {fmtCols.length > 0 && el.type !== "text" && (
          <div className="space-y-1">
            <div className="text-sm font-medium">Number format</div>
            {fmtCols.map((c) => (
              <div key={c} className="flex items-center gap-2 text-sm">
                <span className="w-40 truncate font-mono text-xs">{c}</span>
                <select className={`${inputCls} !w-32`} value={fmtGet(c)} onChange={(e) => fmtSet(c, e.target.value)}>
                  {FORMATS.map((f) => <option key={f} value={f}>{f || "default"}</option>)}
                </select>
              </div>
            ))}
          </div>
        )}

        <Field label="Cache TTL (seconds)">
          <input className={`${inputCls} !w-32`} type="number" min={0} value={el.cache_ttl_seconds}
            onChange={(e) => patch({ cache_ttl_seconds: Math.max(0, Number(e.target.value) || 0) })} />
        </Field>

        {layout && (
          <div>
            <div className="mb-1 text-sm font-medium">Position (12-column grid)</div>
            <div className="grid grid-cols-4 gap-2">
              {(["x", "w", "y", "h"] as const).map((k) => (
                <label key={k} className="text-xs text-slate-500">{k === "x" ? "Column" : k === "w" ? "Width" : k === "y" ? "Row" : "Height"}
                  <input className={inputCls} type="number" value={layout[k]} min={k === "w" || k === "h" ? 1 : 0}
                    onChange={(e) => {
                      let v = Math.floor(Number(e.target.value) || 0);
                      v = Math.max(k === "w" || k === "h" ? 1 : 0, v);
                      if (k === "x") v = Math.min(v, 12 - layout.w);
                      if (k === "w") v = Math.min(v, 12 - layout.x);
                      onLayout({ [k]: v });
                    }} />
                </label>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="mb-1 text-sm font-medium">Preview</div>
          <div className="rounded border border-slate-200 bg-slate-50 p-2">
            <SpecRenderer spec={previewSpec} data={result ? { [el.id]: result } : {}} />
          </div>
        </div>
      </div>

      <div className="sticky bottom-0 mt-auto flex items-center gap-2 border-t border-slate-200 bg-white px-4 py-3">
        <Btn onClick={apply}>Apply to view</Btn>
        {applied && <span className="text-sm text-green-700">Applied. Remember to Save.</span>}
        <Btn variant="ghost" className="ml-auto text-red-600" onClick={() => { if (window.confirm("Remove this element from the view? (Nothing is saved until you click Save.)")) onRemove(); }}>Remove from view</Btn>
      </div>
    </aside>
  );
}

function ColSelect({ cols, value, onChange, allowNone }: { cols: string[]; value: string; onChange: (v: string) => void; allowNone?: boolean }) {
  const options = cols.includes(value) || !value ? cols : [value, ...cols];
  return (
    <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value)}>
      {(allowNone || !value) && <option value="">{allowNone ? "(none)" : "Select…"}</option>}
      {options.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}
