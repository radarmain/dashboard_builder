import { useMemo, useState } from "react";
import { SpecRenderer, columnIsNumeric, type ChartDef, type ChartKind, type ElementData, type LayoutItem, type ViewElement, type ViewSpec } from "@rdb/shared";
import { post } from "../api";
import {
  Box, Checkbox, Divider, FormControlLabel, IconButton, MenuItem, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { Btn, ConfirmDialog, ErrorBox, errMsg, monoSx } from "../ui";

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
  const [confirmRemove, setConfirmRemove] = useState(false);

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

  const section = (title: string) => <Typography variant="subtitle2" sx={{ mb: 1 }}>{title}</Typography>;
  const numField = (label: string, v: number, min: number, onChange: (n: number) => void) => (
    <TextField key={label} label={label} type="number" value={v} slotProps={{ htmlInput: { min } }} onChange={(e) => onChange(Number(e.target.value))} />
  );

  return (
    <Box component="aside" sx={{
      display: "flex", height: "100%", width: 448, flexShrink: 0, flexDirection: "column", overflow: "auto",
      borderLeft: 1, borderColor: "divider", bgcolor: "background.paper",
    }}>
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: 1, borderColor: "divider", px: 2.5, py: 1.5 }}>
        <Typography variant="h6" component="h2" sx={{ textTransform: "capitalize" }}>
          {el.type} <Typography component="span" variant="caption" sx={{ ...monoSx, color: "text.secondary" }}>{el.id}</Typography>
        </Typography>
        <IconButton onClick={onClose} aria-label="Close panel">✕</IconButton>
      </Box>

      <Box sx={{ display: "grid", gap: 2.5, p: 2.5 }}>
        <TextField label="Title" value={el.title} onChange={(e) => patch({ title: e.target.value })} />

        <TextField label="SQL" multiline minRows={7} value={el.sql} onChange={(e) => patch({ sql: e.target.value })}
          placeholder="SELECT channel, SUM(spend) AS spend FROM `project.dataset.table` GROUP BY channel"
          helperText="Only SELECT on this project's connected tables, using fully qualified `project.dataset.table` names."
          slotProps={{ htmlInput: { spellCheck: false, style: { fontFamily: monoSx.fontFamily, fontSize: 12 } } }} />
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Btn onClick={run} disabled={running}>{running ? "Running…" : "Run"}</Btn>
          <FormControlLabel control={<Checkbox checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />}
            label={<Typography variant="body2">Refresh (skip cache)</Typography>} />
        </Box>
        <ErrorBox error={error} />

        {result && (
          <Box sx={{ display: "grid", gap: 1 }}>
            <Typography variant="caption" color="text.secondary">
              {result.rows.length.toLocaleString()} rows{result.truncated ? " (truncated)" : ""}
              {result.cache_hit !== undefined && ` · ${result.cache_hit ? "cached" : "fresh"}`}
              {result.bytes_processed !== undefined && ` · ${(result.bytes_processed / 1e6).toFixed(1)} MB processed`}
            </Typography>
            <TableContainer sx={{ maxHeight: 176, border: 1, borderColor: "divider", borderRadius: "12px" }}>
              <Table size="small" stickyHeader sx={{ "& td, & th": { fontSize: 12, py: 0.5, px: 1 } }}>
                <TableHead><TableRow>{result.columns.map((c) => <TableCell key={c} sx={{ whiteSpace: "nowrap" }}>{c}</TableCell>)}</TableRow></TableHead>
                <TableBody>
                  {result.rows.slice(0, 20).map((r, i) => (
                    <TableRow key={i}>
                      {r.map((v, j) => <TableCell key={j} sx={{ maxWidth: 192, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v === null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v)}</TableCell>)}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        )}

        {el.type === "chart" && el.chart && (
          <Box sx={{ display: "grid", gap: 2, p: 2, border: 1, borderColor: "divider", borderRadius: "16px" }}>
            {section("Chart")}
            <TextField select label="Kind" value={el.chart.kind} onChange={(e) => patchChart({ kind: e.target.value as ChartKind })}>
              {KINDS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
            </TextField>
            <ColSelect label={el.chart.kind === "pie" ? "Label column" : "X column"} cols={columns} value={el.chart.x} onChange={(v) => patchChart({ x: v })} />
            <Box>
              <Typography variant="body2" sx={{ mb: 0.5 }}>{el.chart.kind === "pie" ? "Value column" : "Y columns"}</Typography>
              {columns.length ? (
                <Box sx={{ display: "flex", flexWrap: "wrap", columnGap: 2 }}>
                  {columns.map((c) => (
                    <FormControlLabel key={c} label={<Typography variant="body2">{c}</Typography>}
                      control={<Checkbox checked={el.chart!.y.includes(c)}
                        onChange={() => patchChart({ y: el.chart!.y.includes(c) ? el.chart!.y.filter((x) => x !== c) : [...el.chart!.y, c] })} />} />
                  ))}
                </Box>
              ) : <Typography variant="caption" color="text.secondary">Run the query to choose columns{el.chart.y.length ? ` (current: ${el.chart.y.join(", ")})` : ""}</Typography>}
            </Box>
            {el.chart.kind !== "pie" && (
              <ColSelect label="Series (split by)" cols={columns} value={el.chart.series ?? ""} allowNone onChange={(v) => patchChart({ series: v || null })} />
            )}
            {(el.chart.kind === "bar" || el.chart.kind === "area") && (
              <FormControlLabel label="Stacked" control={<Checkbox checked={el.chart.stacked} onChange={(e) => patchChart({ stacked: e.target.checked })} />} />
            )}
          </Box>
        )}

        {fmtCols.length > 0 && el.type !== "text" && (
          <Box sx={{ display: "grid", gap: 1.5 }}>
            {section("Number format")}
            {fmtCols.map((c) => (
              <Box key={c} sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
                <Typography variant="caption" noWrap title={c} sx={{ ...monoSx, flex: 1 }}>{c}</Typography>
                <TextField select value={fmtGet(c)} onChange={(e) => fmtSet(c, e.target.value)} sx={{ width: 140 }} aria-label={`Format for ${c}`}>
                  {FORMATS.map((f) => <MenuItem key={f} value={f}>{f || "default"}</MenuItem>)}
                </TextField>
              </Box>
            ))}
          </Box>
        )}

        <TextField label="Cache TTL (seconds)" type="number" value={el.cache_ttl_seconds} sx={{ maxWidth: 200 }} slotProps={{ htmlInput: { min: 0 } }}
          onChange={(e) => patch({ cache_ttl_seconds: Math.max(0, Number(e.target.value) || 0) })} />

        {layout && (
          <Box>
            {section("Position (12-column grid)")}
            <Box sx={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 1 }}>
              {(["x", "w", "y", "h"] as const).map((k) => numField(
                k === "x" ? "Column" : k === "w" ? "Width" : k === "y" ? "Row" : "Height", layout[k], k === "w" || k === "h" ? 1 : 0,
                (n) => {
                  let v = Math.floor(n || 0);
                  v = Math.max(k === "w" || k === "h" ? 1 : 0, v);
                  if (k === "x") v = Math.min(v, 12 - layout.w);
                  if (k === "w") v = Math.min(v, 12 - layout.x);
                  onLayout({ [k]: v });
                },
              ))}
            </Box>
          </Box>
        )}

        <Box>
          {section("Preview")}
          <Box sx={{ borderRadius: "12px", bgcolor: (t) => t.palette.tokens.surfaceContainerLow, p: 1 }}>
            <SpecRenderer spec={previewSpec} data={result ? { [el.id]: result } : {}} />
          </Box>
        </Box>
      </Box>

      <Divider sx={{ mt: "auto" }} />
      <Box sx={{ position: "sticky", bottom: 0, display: "flex", alignItems: "center", gap: 1.5, bgcolor: "background.paper", px: 2.5, py: 1.5 }}>
        <Btn onClick={apply}>Apply to view</Btn>
        {applied && <Typography variant="body2" sx={{ color: "success.main" }}>Applied. Remember to Save.</Typography>}
        <Btn variant="ghost" color="error" sx={{ ml: "auto" }} onClick={() => setConfirmRemove(true)}>Remove from view</Btn>
      </Box>
      <ConfirmDialog open={confirmRemove} destructive title="Remove this element?" confirmLabel="Remove"
        body="It is removed from the view. Nothing is saved until you click Save."
        onConfirm={() => { setConfirmRemove(false); onRemove(); }} onCancel={() => setConfirmRemove(false)} />
    </Box>
  );
}

function ColSelect({ label, cols, value, onChange, allowNone }: { label: string; cols: string[]; value: string; onChange: (v: string) => void; allowNone?: boolean }) {
  const options = cols.includes(value) || !value ? cols : [value, ...cols];
  return (
    <TextField select label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      {(allowNone || !value) && <MenuItem value="">{allowNone ? "(none)" : "Select…"}</MenuItem>}
      {options.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
    </TextField>
  );
}
