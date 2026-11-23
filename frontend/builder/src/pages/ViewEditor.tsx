import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { SpecRenderer, type DataEntry, type DataMap, type ElementData, type ElementType, type ViewElement, type ViewSpec } from "@rdb/shared";
import { ApiError, get, post, type ChatOp, type GateInfo, type PublishGuard, type ViewSummary } from "../api";
import { AppBar, Box, IconButton, Menu, MenuItem, Snackbar, Toolbar, Tooltip, Typography } from "@mui/material";
import { Badge, Btn, ConfirmDialog, ErrorBox, Loading, errMsg, viewStatus } from "../ui";
import { GateDialog, fmtBytes, gateFrom } from "./RunGate";
import { ChatPanel } from "./ChatPanel";
import { ElementPanel } from "./ElementPanel";
import { ImportPanel } from "./ImportPanel";
import { RevisionsDrawer } from "./RevisionsDrawer";
import { ViewSettingsDialog } from "./ViewSettingsDialog";
import { publicViewUrl } from "@rdb/portal";
import { ClientPreviewButton, CopyLinkButton, PREVIEW_BASE } from "../Preview";

const DEFAULT_H: Record<ElementType, number> = { chart: 6, table: 6, kpi: 3, text: 2 };

function emptySpec(name: string, slug: string): ViewSpec {
  return { schema_version: 1, view: { name, slug }, revision: 0, updated_at: "", updated_by: "", layout: [], elements: [] };
}

/** What counts as "unsaved changes": everything the analyst edits. */
const fingerprint = (s: ViewSpec) => JSON.stringify({ n: s.view.name, l: s.layout, e: s.elements });

export function ViewEditor() {
  const { client = "", project = "", view = "" } = useParams();
  const navigate = useNavigate();
  const base = `/clients/${client}/projects/${project}`;
  const viewPath = `${base}/views/${view}`;
  const [search, setSearch] = useSearchParams();
  const [importing, setImporting] = useState(search.get("import") === "1");

  const [meta, setMeta] = useState<ViewSummary | null>(null);
  const [spec, setSpec] = useState<ViewSpec | null>(null);
  const [saved, setSaved] = useState("");
  const [data, setData] = useState<DataMap>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "publish" | null>(null);
  const [showRevisions, setShowRevisions] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [addAnchor, setAddAnchor] = useState<HTMLElement | null>(null);
  const [gate, setGate] = useState<{ ids: string[]; info: GateInfo } | null>(null); // run needs an explicit OK
  const [guard, setGuard] = useState<PublishGuard | null>(null); // publish with unreviewed imported elements
  const runImportedRef = useRef<(id: string, confirm?: boolean) => Promise<void>>(async () => {});

  const [undoStack, setUndoStack] = useState<ViewSpec[]>([]); // spec before each of the last 10 AI responses
  const [touched, setTouched] = useState<Record<string, number>>({}); // bumps remount the panel when chat edits an element
  const specRef = useRef<ViewSpec | null>(null);
  specRef.current = spec;
  const lastTurn = useRef(0);

  const dirty = !!spec && fingerprint(spec) !== saved;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  /** Imported elements are lazy: no query runs until the analyst presses Run (dry-run estimate shown). */
  const idleEntry = useCallback((el: ViewElement): DataEntry => ({
    status: "idle",
    text: `Not run yet. Estimated ${fmtBytes(el.import?.dry_run_bytes)}${el.import?.source_is_view ? " (view source)" : ""}.`,
    onRun: () => void runImportedRef.current(el.id),
  }), []);

  const loadData = useCallback(async (s: ViewSpec) => {
    const all = s.elements.filter((e) => e.sql.trim() && e.type !== "text");
    const lazy = all.filter((e) => e.import);
    const runnable = all.filter((e) => !e.import);
    setData({
      ...Object.fromEntries(lazy.map((e) => [e.id, idleEntry(e)])),
      ...Object.fromEntries(runnable.map((e) => [e.id, { status: "loading" as const }])),
    });
    await Promise.all(runnable.map(async (e) => {
      try {
        const d = await get<ElementData>(`${viewPath}/data/${e.id}?draft=true`);
        setData((m) => ({ ...m, [e.id]: { status: "ok", data: d } }));
      } catch (err) {
        setData((m) => ({ ...m, [e.id]: { status: "error", message: errMsg(err) } }));
      }
    }));
  }, [viewPath, idleEntry]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const m = await get<ViewSummary>(viewPath);
      if (m.project_slug !== project || m.slug !== view) { // reached via an old link: swap in the canonical URL
        navigate(`/c/${client}/p/${m.project_slug}/v/${m.slug}`, { replace: true });
        return;
      }
      setMeta(m);
      let s: ViewSpec;
      try {
        s = await get<ViewSpec>(`${viewPath}/draft`);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e;
        s = emptySpec(m.name, m.slug); // brand-new view: no draft yet
      }
      setSpec(s);
      setSaved(fingerprint(s));
      setUndoStack([]);
      setSelectedId(null);
      void loadData(s);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [viewPath, loadData, client, project, view, navigate]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (meta) document.title = `${meta.name} · RADaR Dashboard Builder`;
  }, [meta?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  // Warn before leaving with unsaved changes (tab close/reload + in-app links).
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => { if (dirtyRef.current) { e.preventDefault(); e.returnValue = ""; } };
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest("a[href]");
      if (a && dirtyRef.current && !window.confirm("You have unsaved changes. Leave without saving?")) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", onClick, true);
    return () => { window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", onClick, true); };
  }, []);

  const updateElement = (el: ViewElement, result?: ElementData) => {
    setSpec((s) => s && { ...s, elements: s.elements.map((x) => (x.id === el.id ? el : x)) });
    if (result) setData((m) => ({ ...m, [el.id]: { status: "ok", data: result } }));
  };

  /** Run an element's SQL through the validated /query path and show it on the canvas. */
  async function fetchElementData(el: ViewElement) {
    if (el.type === "text" || !el.sql.trim()) return;
    if (el.import) { setData((m) => ({ ...m, [el.id]: idleEntry(el) })); return; }
    setData((m) => ({ ...m, [el.id]: { status: "loading" } }));
    try {
      const d = await post<ElementData>(`${base}/query`, { sql: el.sql });
      setData((m) => ({ ...m, [el.id]: { status: "ok", data: d } }));
    } catch (err) {
      setData((m) => ({ ...m, [el.id]: { status: "error", message: errMsg(err) } }));
    }
  }

  /** Run an imported element through /query with the dry-run gate (views / big scans ask first). */
  async function runImported(id: string, confirm = false) {
    const el = specRef.current?.elements.find((e) => e.id === id);
    if (!el) return;
    setData((m) => ({ ...m, [id]: { status: "loading" } }));
    try {
      const d = await post<ElementData>(`${base}/query`, { sql: el.sql, gate: true, confirm });
      setData((m) => ({ ...m, [id]: { status: "ok", data: d } }));
    } catch (err) {
      const info = gateFrom(err);
      if (info) { setData((m) => ({ ...m, [id]: idleEntry(el) })); setGate({ ids: [id], info }); return; }
      setData((m) => ({ ...m, [id]: { status: "error", message: errMsg(err) } }));
    }
  }
  runImportedRef.current = runImported;

  const idle = (spec?.elements ?? []).filter((e) => { const d = data[e.id]; return e.import && d && "status" in d && d.status === "idle"; });
  const idleBytes = idle.reduce((n, e) => n + (e.import?.dry_run_bytes ?? 0), 0);
  function runAllInfo(): GateInfo {
    return {
      needs_confirmation: true,
      estimated_bytes: idleBytes,
      source_is_view: idle.some((e) => e.import?.source_is_view),
      base_tables: [...new Set(idle.flatMap((e) => e.import?.base_tables ?? []))],
      reasons: [`${idle.length} imported element${idle.length === 1 ? "" : "s"} will run`, ...(idle.some((e) => e.import?.source_is_view) ? ["some sources are views (every run re-executes their query)"] : [])],
      suggestion: "Consider a materialized table for the base data; views can be slow and costly.",
    };
  }

  /** Apply validated AI operations to the LOCAL draft only. Nothing is saved or published. */
  function applyChatOps(ops: ChatOp[], turnId: number) {
    const cur = specRef.current;
    if (!cur) return;
    if (lastTurn.current !== turnId) {
      lastTurn.current = turnId;
      setUndoStack((st) => [...st.slice(-9), cur]);
    }
    let next = cur;
    const refetch: ViewElement[] = [];
    for (const op of ops) {
      if (op.op === "add_element") {
        if (next.elements.some((e) => e.id === op.element.id)) continue;
        next = { ...next, elements: [...next.elements, op.element], layout: [...next.layout, { element_id: op.element.id, ...op.layout }] };
        refetch.push(op.element);
        setTouched((t) => ({ ...t, [op.element.id]: (t[op.element.id] ?? 0) + 1 }));
      } else if (op.op === "update_element") {
        next = {
          ...next,
          elements: next.elements.map((e) => (e.id === op.element.id ? op.element : e)),
          layout: op.layout ? next.layout.map((l) => (l.element_id === op.element.id ? { ...l, ...op.layout } : l)) : next.layout,
        };
        refetch.push(op.element);
        setTouched((t) => ({ ...t, [op.element.id]: (t[op.element.id] ?? 0) + 1 }));
      } else {
        next = { ...next, elements: next.elements.filter((e) => e.id !== op.id), layout: next.layout.filter((l) => l.element_id !== op.id) };
        setSelectedId((sel) => (sel === op.id ? null : sel));
      }
    }
    specRef.current = next;
    setSpec(next);
    refetch.forEach((e) => void fetchElementData(e));
  }

  function undoAi() {
    const prev = undoStack[undoStack.length - 1];
    const cur = specRef.current;
    if (!prev || !cur) return;
    setUndoStack((st) => st.slice(0, -1));
    specRef.current = prev;
    setSpec(prev);
    const before = new Map(cur.elements.map((e) => [e.id, e.sql]));
    prev.elements.filter((e) => before.get(e.id) !== e.sql).forEach((e) => void fetchElementData(e));
    const was = new Map(cur.elements.map((e) => [e.id, JSON.stringify(e)]));
    setTouched((t) => ({ ...t, ...Object.fromEntries(prev.elements.filter((e) => was.get(e.id) !== JSON.stringify(e)).map((e) => [e.id, (t[e.id] ?? 0) + 1])) }));
    setSelectedId((sel) => (sel && prev.elements.some((e) => e.id === sel) ? sel : null));
  }

  function addElement(type: ElementType) {
    if (!spec) return;
    const n = Math.max(0, ...spec.elements.map((e) => Number(e.id.replace(/^el_/, "")) || 0)) + 1;
    const id = `el_${n}`;
    const y = Math.max(0, ...spec.layout.map((l) => l.y + l.h));
    const el: ViewElement = {
      id, type, title: "", sql: "", format: {}, cache_ttl_seconds: 3600,
      chart: type === "chart" ? { kind: "bar", x: "", y: [], series: null, stacked: false, format: {} } : null,
    };
    setSpec({ ...spec, elements: [...spec.elements, el], layout: [...spec.layout, { element_id: id, x: 0, y, w: 12, h: DEFAULT_H[type] }] });
    setSelectedId(id);
    setAddAnchor(null);
  }

  function removeFromView(id: string) {
    setSpec((s) => s && { ...s, elements: s.elements.filter((e) => e.id !== id), layout: s.layout.filter((l) => l.element_id !== id) });
    setSelectedId(null);
  }

  function setLayout(id: string, patch: Partial<{ x: number; y: number; w: number; h: number }>) {
    setSpec((s) => s && { ...s, layout: s.layout.map((l) => (l.element_id === id ? { ...l, ...patch } : l)) });
  }

  async function save() {
    if (!spec) return;
    setError(null); setNotice(null);
    const empty = spec.elements.find((e) => e.type !== "text" && !e.sql.trim());
    if (empty) { setError(`Element ${empty.id}${empty.title ? ` ("${empty.title}")` : ""} has no SQL. Add SQL or remove it before saving.`); setSelectedId(empty.id); return; }
    const badChart = spec.elements.find((e) => e.type === "chart" && (!e.chart?.x || !e.chart.y.length));
    if (badChart) { setError(`Chart ${badChart.id} needs an x column and at least one y column. Run its SQL, then pick columns.`); setSelectedId(badChart.id); return; }
    setBusy("save");
    try {
      const r = await post<{ revision: number }>(`${viewPath}/save`, spec);
      setSaved(fingerprint(spec));
      setNotice(`Saved as revision ${r.revision}`);
      setMeta(await get<ViewSummary>(viewPath));
    } catch (e) { setError(errMsg(e)); } finally { setBusy(null); }
  }

  async function publish(confirmUnreviewed = false) {
    setError(null); setNotice(null); setBusy("publish");
    try {
      const r = await post<{ published_revision: number }>(`${viewPath}/publish`, { confirm_unreviewed: confirmUnreviewed });
      setNotice(`Published revision ${r.published_revision}`);
      setMeta(await get<ViewSummary>(viewPath));
    } catch (e) {
      const g = e instanceof ApiError && e.status === 409 ? (e.detail as PublishGuard | undefined) : undefined;
      if (g?.reason === "unreviewed_imports") setGuard(g);
      else setError(`Publish refused: ${errMsg(e)}`);
    } finally { setBusy(null); }
  }

  const selected = useMemo(() => spec?.elements.find((e) => e.id === selectedId) ?? null, [spec, selectedId]);
  const selectedLayout = spec?.layout.find((l) => l.element_id === selectedId) ?? null;
  const canPublish = !!meta && meta.draft_revision !== null && meta.has_unpublished_changes && !dirty && !busy;

  if (!spec || !meta) return <Box sx={{ p: 4 }}>{error ? <ErrorBox error={error} /> : <Loading />}</Box>;

  return (
    <Box sx={{ display: "flex", height: "100%", flexDirection: "column" }}>
      <AppBar position="static">
        <Toolbar sx={{ gap: 1.5, flexWrap: "wrap" }}>
          <Typography variant="h6" component="h1" noWrap sx={{ minWidth: 0 }}>{meta.name}</Typography>
          {viewStatus(meta)}
          {dirty && <Badge tone="blue">Unsaved edits</Badge>}
          <Tooltip title="View settings (name, URL, description)">
            <IconButton aria-label="View settings" size="small" onClick={() => setShowSettings(true)}>
              <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
            </IconButton>
          </Tooltip>
          <Box sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 1 }}>
            <CopyLinkButton label="Copy client link" url={publicViewUrl(client, meta.project_slug, meta.slug)} />
            <ClientPreviewButton to={`${PREVIEW_BASE}/c/${client}/p/${meta.project_slug}/v/${meta.slug}`} />
            <Btn variant="ghost" onClick={() => setShowRevisions(true)}>Revisions</Btn>
            <Btn variant="secondary" onClick={save} disabled={!!busy || !dirty}>{busy === "save" ? "Saving…" : "Save"}</Btn>
            <Btn onClick={() => void publish()} disabled={!canPublish} title={dirty ? "Save your edits first" : !meta.has_unpublished_changes ? "Nothing to publish" : ""}>
              {busy === "publish" ? "Publishing…" : "Publish"}
            </Btn>
          </Box>
        </Toolbar>
      </AppBar>
      {error && <Box sx={{ px: 2.5, pt: 1.5 }}><ErrorBox error={error} onClose={() => setError(null)} /></Box>}
      <Snackbar open={!!notice} autoHideDuration={4000} onClose={(_, reason) => reason !== "clickaway" && setNotice(null)}
        message={notice} anchorOrigin={{ vertical: "bottom", horizontal: "center" }} />

      <Box sx={{ display: "flex", minHeight: 0, flex: 1 }}>
        <Box sx={{ display: "flex", minWidth: 0, flex: 1, flexDirection: "column" }}>
          <Box sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
            <Box sx={{ mb: 1.5, display: "flex", alignItems: "center", gap: 1.5 }}>
              <Btn variant="secondary" onClick={(e) => setAddAnchor(e.currentTarget)} aria-haspopup="menu">+ Add element</Btn>
              <Menu anchorEl={addAnchor} open={!!addAnchor} onClose={() => setAddAnchor(null)}>
                {(["chart", "table", "kpi"] as const).map((t) => (
                  <MenuItem key={t} onClick={() => addElement(t)} sx={{ textTransform: "capitalize", minWidth: 140 }}>{t}</MenuItem>
                ))}
              </Menu>
              {idle.length > 0 && (
                <Btn variant="secondary" onClick={() => setGate({ ids: idle.map((e) => e.id), info: runAllInfo() })}>
                  Run all ({idle.length}, ~{fmtBytes(idleBytes)})
                </Btn>
              )}
              <Typography variant="caption" color="text.secondary">Click an element to edit it.</Typography>
            </Box>
            {spec.elements.length === 0 ? (
              <Box sx={{ border: 2, borderStyle: "dashed", borderColor: "divider", borderRadius: "16px", p: 8, textAlign: "center", color: "text.secondary" }}>
                Empty view. Ask the AI below for a chart, or use “+ Add element”.
              </Box>
            ) : (
              <SpecRenderer spec={spec} data={data} selectedId={selectedId} onSelect={setSelectedId} popVersions={touched} />
            )}
          </Box>
          {importing && (
            <ImportPanel base={base} viewName={meta.name} getSpec={() => specRef.current as ViewSpec}
              onChanges={applyChatOps} onUndo={undoAi} undoCount={undoStack.length}
              onClose={() => { setImporting(false); setSearch({}, { replace: true }); }} />
          )}
          <ChatPanel chatPath={`${viewPath}/chat`} getSpec={() => specRef.current as ViewSpec} onChanges={applyChatOps} onUndo={undoAi} undoCount={undoStack.length} />
        </Box>

        {selected && (
          <ElementPanel
            key={`${selected.id}:${touched[selected.id] ?? 0}`}
            element={selected}
            layout={selectedLayout}
            queryPath={`${base}/query`}
            initialData={(() => { const d = data[selected.id]; return d && "status" in d ? (d.status === "ok" ? d.data : null) : (d ?? null); })()}
            onApply={updateElement}
            onLayout={(patch) => setLayout(selected.id, patch)}
            onRemove={() => removeFromView(selected.id)}
            onClose={() => setSelectedId(null)}
          />
        )}
      </Box>

      <GateDialog info={gate?.info ?? null} title={gate && gate.ids.length > 1 ? "Run all imported elements?" : "Run this query?"}
        onCancel={() => setGate(null)}
        onConfirm={() => { const ids = gate?.ids ?? []; setGate(null); ids.forEach((id) => void runImported(id, true)); }} />
      <ConfirmDialog open={!!guard} title="Publish with unreviewed imported elements?" confirmLabel="Publish anyway"
        onCancel={() => setGuard(null)}
        onConfirm={() => { setGuard(null); void publish(true); }}
        body={guard && (
          <>
            <Typography variant="body2" sx={{ mb: 1 }}>These imported elements have not been verified or marked reviewed. Clients will see them as-is. Your confirmation is recorded in the audit log.</Typography>
            {guard.elements.map((e) => <Typography key={e.id} variant="body2">• {e.title || e.sheet || e.id} ({e.status.replace("_", " ")})</Typography>)}
          </>
        )} />
      {showSettings && (
        <ViewSettingsDialog
          viewPath={viewPath}
          name={meta.name}
          slug={meta.slug}
          dirty={dirty}
          description={meta.description ?? ""}
          onClose={() => setShowSettings(false)}
          onSaved={(m) => {
            setShowSettings(false);
            if (m.slug && m.slug !== view) { // renamed: move to the new route, sidebar cache is invalidated by the PATCH
              setMeta((cur) => (cur ? { ...cur, ...m } : cur));
              navigate(`/c/${client}/p/${m.project_slug ?? project}/v/${m.slug}`, { replace: true });
              setNotice("View URL updated. The old link keeps working.");
            } else {
              setMeta((cur) => (cur ? { ...cur, ...m } : cur));
              setNotice("View settings saved");
            }
          }}
        />
      )}
      {showRevisions && (
        <RevisionsDrawer
          viewPath={viewPath}
          dirty={dirty}
          onClose={() => setShowRevisions(false)}
          onRestored={async () => { setShowRevisions(false); await load(); setNotice("Restored as a new draft revision"); }}
        />
      )}
    </Box>
  );
}
