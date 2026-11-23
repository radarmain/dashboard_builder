import { useEffect, useRef, useState } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, LinearProgress, Typography } from "@mui/material";
import type { ViewSpec } from "@rdb/shared";
import { ApiError, get, streamChat, type ChatEvent, type ChatOp, type EntryStatus, type GateInfo, type ImportEntry, type PlanResponse, type PlanSheet } from "../api";
import { Badge, Btn, ErrorBox, errMsg, monoSx } from "../ui";
import { GateDialog, fmtBytes } from "./RunGate";

const ENTRY_TONE: Record<EntryStatus, "gray" | "green" | "amber" | "blue"> = { unverified: "blue", needs_review: "amber", verified: "green", differs: "amber", skipped: "gray" };

const TURN_BASE = 100000; // import turn ids never collide with chat turn ids (undo = one step per sheet)

type SheetState = "waiting" | "running" | "done" | "skipped" | "failed";

function pct(n?: number) {
  return n === undefined ? "" : `${(n * 100).toFixed(2)}%`;
}

function Recon({ r }: { r: ImportEntry["reconciliation"] }) {
  if (r.matched_rows !== undefined)
    return (
      <Box>
        <Typography variant="body2">
          Matched {r.matched_rows}, mismatched {r.mismatched_rows}, only in Tableau {r.rows_only_in_reference}, only in BigQuery {r.rows_only_in_result};
          max difference {pct(r.max_relative_difference)} (tolerance 0.5%){r.duplicate_rows_collapsed ? `; ${r.duplicate_rows_collapsed} dual-axis duplicate rows collapsed` : ""}.
          {" "}<strong>{r.ok ? "Reconciled" : "Differences found"}</strong>
        </Typography>
        {!!r.examples?.length && <Typography variant="caption" component="pre" sx={monoSx}>{r.examples.map((e) => JSON.stringify(e)).join("\n")}</Typography>}
      </Box>
    );
  if (r.error) return <Typography variant="body2" color="error">{r.error}</Typography>;
  return (
    <Box>
      <Typography variant="body2">{r.result ?? "no reconciliation"}</Typography>
    </Box>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <Box>
      <Typography variant="subtitle2">{title}</Typography>
      {items.map((t, i) => <Typography key={i} variant="body2">• {t}</Typography>)}
    </Box>
  );
}

function ReportCard({ e, onVerify, canVerify }: { e: ImportEntry; onVerify: () => void; canVerify: boolean }) {
  return (
    <Accordion disableGutters variant="outlined">
      <AccordionSummary>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
          <Typography variant="subtitle2">{e.sheet}</Typography>
          <Badge tone={ENTRY_TONE[e.status]}>{e.status.replace("_", " ")}</Badge>
          {e.dry_run_bytes != null && <Typography variant="caption" color="text.secondary">est. {fmtBytes(e.dry_run_bytes)}{e.source_is_view ? ", view" : ""}</Typography>}
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ display: "grid", gap: 1.5 }}>
        {e.status_reason && <Typography variant="body2" color="text.secondary">{e.status_reason}</Typography>}
        {e.sql && <Typography component="pre" variant="caption" sx={{ ...monoSx, whiteSpace: "pre-wrap", m: 0 }}>{e.sql}</Typography>}
        {!!e.result_columns?.length && <Typography variant="caption" sx={monoSx}>Columns: {e.result_columns.map((c) => `${c.name} ${c.type}`).join(", ")}</Typography>}
        {e.config && <Typography component="pre" variant="caption" sx={{ ...monoSx, whiteSpace: "pre-wrap", m: 0 }}>{JSON.stringify(e.config)}</Typography>}
        <List title="Filters baked into the SQL (not interactive yet)" items={e.filters_baked} />
        <List title="Translation notes" items={e.notes ?? e.assumptions} />
        {e.base_tables && e.base_tables.length > 0 && <Typography variant="caption" sx={monoSx}>Base tables: {e.base_tables.join(", ")}</Typography>}
        <Box>
          <Typography variant="subtitle2">Verify against Tableau (opt-in, informational)</Typography>
          <Recon r={e.reconciliation} />
          {e.element_id && <Btn variant="secondary" size="small" disabled={!canVerify} onClick={onVerify} sx={{ mt: 0.5 }}>Verify this sheet</Btn>}
        </Box>
        <List title="Warnings" items={e.warnings} />
        <Typography variant="caption" color="text.secondary">Scanned {(e.bytes_scanned / 1e9).toFixed(2)} GB in this step</Typography>
      </AccordionDetails>
    </Accordion>
  );
}

/** Runs the import sheet by sheet; each result is applied to the LOCAL draft like a chat result. */
export function ImportPanel({ base, viewName, getSpec, onChanges, onUndo, undoCount, onClose }: {
  base: string; viewName: string; getSpec: () => ViewSpec;
  onChanges: (ops: ChatOp[], turnId: number) => void; onUndo: () => void; undoCount: number; onClose: () => void;
}) {
  const [resp, setResp] = useState<PlanResponse | null>(null);
  const [state, setState] = useState<Record<string, SheetState>>({});
  const [step, setStep] = useState("Reading the Tableau workbook…");
  const [entries, setEntries] = useState<ImportEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(true);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [pending, setPending] = useState<(GateInfo & { sheet: string })[]>([]); // verify runs awaiting an explicit OK
  const verifyTurn = useRef(TURN_BASE + 5000);
  const started = useRef(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const ctl = new AbortController();
    abort.current = ctl;
    void (async () => {
      try {
        const r = await get<PlanResponse>(`${base}/tableau-import/plan`);
        setResp(r);
        const order = [...r.plan.worksheets].sort((a, b) => (a.grid?.y ?? 99) - (b.grid?.y ?? 99) || (a.grid?.x ?? 0) - (b.grid?.x ?? 0));
        setState(Object.fromEntries(order.map((s) => [s.name, s.status === "unsupported" ? "skipped" : "waiting"])));
        let turn = TURN_BASE;
        for (const s of order as PlanSheet[]) {
          if (s.status === "unsupported" || ctl.signal.aborted) continue;
          setState((m) => ({ ...m, [s.name]: "running" }));
          const id = ++turn;
          let failed = false;
          try {
            await streamChat(
              `${base}/tableau-import/sheets/${encodeURIComponent(s.name)}/run`,
              { import_id: r.import_id, current_spec: getSpec(), view_name: viewName },
              ctl.signal,
              (e: ChatEvent) => {
                if (e.type === "status") setStep(`${s.name}: ${e.text}`);
                else if (e.type === "changes") onChanges(e.operations, id);
                else if (e.type === "report") setEntries((all) => [...all.filter((x) => x.sheet !== e.entry.sheet), e.entry]);
                else if (e.type === "error") { failed = true; setError(`${s.name}: ${e.message}`); }
              },
            );
          } catch (err) {
            if (ctl.signal.aborted) break;
            failed = true;
            setError(`${s.name}: ${err instanceof ApiError ? err.message : errMsg(err)}`);
          }
          setState((m) => ({ ...m, [s.name]: failed ? "failed" : "done" }));
        }
      } catch (err) {
        if (!ctl.signal.aborted) setError(errMsg(err));
      } finally {
        setRunning(false);
        setStep(ctl.signal.aborted ? "Stopped." : "Finished.");
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /** VERIFY (opt-in): one query within the byte budget + one comparison. A difference is a finding. */
  async function verify(sheet: string, confirm = false) {
    if (!resp) return;
    setVerifying(sheet); setError(null);
    try {
      await streamChat(
        `${base}/tableau-import/sheets/${encodeURIComponent(sheet)}/verify`,
        { import_id: resp.import_id, current_spec: getSpec(), confirm },
        new AbortController().signal,
        (e: ChatEvent) => {
          if (e.type === "changes") onChanges(e.operations, ++verifyTurn.current);
          else if (e.type === "report") setEntries((all) => [...all.filter((x) => x.sheet !== e.entry.sheet), e.entry]);
          else if (e.type === "needs_confirmation") setPending((q) => [...q, e]);
          else if (e.type === "error") setError(e.message);
        },
      );
    } catch (err) {
      setError(`${sheet}: ${err instanceof ApiError ? err.message : errMsg(err)}`);
    } finally {
      setVerifying(null);
    }
  }
  async function verifyAll() {
    for (const e of entries) if (e.element_id) await verify(e.sheet);
  }

  const total = Object.values(state).filter((s) => s !== "skipped").length;
  const done = Object.values(state).filter((s) => s === "done" || s === "failed").length;
  const skipped = resp?.plan.worksheets.filter((s) => s.status === "unsupported") ?? [];

  return (
    <Box sx={{ borderTop: 1, borderColor: "divider", p: 2, maxHeight: "45vh", overflow: "auto", display: "grid", gap: 1.5 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <Typography variant="h6" component="h2" sx={{ flex: 1 }}>Import from Tableau{resp ? `: ${resp.workbook.workbook_name}` : ""}</Typography>
        {running && <Btn variant="secondary" size="small" onClick={() => abort.current?.abort()}>Stop</Btn>}
        <Btn variant="ghost" size="small" disabled={undoCount === 0 || running} onClick={onUndo}>Undo last sheet{undoCount ? ` (${undoCount})` : ""}</Btn>
        <Btn variant="ghost" size="small" disabled={running} onClick={onClose}>Close</Btn>
      </Box>
      <Alert severity="info">
        STRUCTURE import: SQL, chart settings, filters and layout are drafted from the workbook's own definitions using dry runs only (zero bytes scanned, no data loaded).
        Elements are marked unverified; press Run on each to load data, then review it. Results are applied to this unsaved draft; nothing is saved or published automatically.
      </Alert>
      {running && <Box><LinearProgress variant={total ? "determinate" : "indeterminate"} value={total ? (done / total) * 100 : 0} /><Typography variant="caption">{step} ({done}/{total || "?"})</Typography></Box>}
      {!running && <Typography variant="body2">{step} {done}/{total} sheets processed.</Typography>}
      <ErrorBox error={error} onClose={() => setError(null)} />
      {skipped.length > 0 && (
        <Typography variant="body2" color="text.secondary">
          Skipped (unsupported): {skipped.map((s) => `${s.name} (${s.status_reason})`).join("; ")}
        </Typography>
      )}
      {entries.some((e) => e.element_id) && (
        <Box><Btn variant="secondary" size="small" disabled={running || !!verifying} onClick={() => void verifyAll()}>Verify all against Tableau (opt-in)</Btn></Box>
      )}
      {entries.map((e) => <ReportCard key={e.sheet} e={e} canVerify={!running && !verifying} onVerify={() => void verify(e.sheet)} />)}
      <GateDialog info={pending[0] ?? null} title={`Verify "${pending[0]?.sheet ?? ""}"?`} confirmLabel="Verify anyway"
        onCancel={() => setPending((q) => q.slice(1))}
        onConfirm={() => { const n = pending[0]; setPending((q) => q.slice(1)); if (n) void verify(n.sheet, true); }} />
    </Box>
  );
}
