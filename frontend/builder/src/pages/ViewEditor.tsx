import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { SpecRenderer, type DataMap, type ElementData, type ElementType, type ViewElement, type ViewSpec } from "@rdb/shared";
import { ApiError, get, post, type ViewSummary } from "../api";
import { Badge, Btn, ErrorBox, errMsg, viewStatus } from "../ui";
import { ElementPanel } from "./ElementPanel";
import { RevisionsDrawer } from "./RevisionsDrawer";

const DEFAULT_H: Record<ElementType, number> = { chart: 6, table: 6, kpi: 3, text: 2 };

function emptySpec(name: string, slug: string): ViewSpec {
  return { schema_version: 1, view: { name, slug }, revision: 0, updated_at: "", updated_by: "", layout: [], elements: [] };
}

/** What counts as "unsaved changes": everything the analyst edits. */
const fingerprint = (s: ViewSpec) => JSON.stringify({ n: s.view.name, l: s.layout, e: s.elements });

export function ViewEditor() {
  const { client = "", project = "", view = "" } = useParams();
  const base = `/clients/${client}/projects/${project}`;
  const viewPath = `${base}/views/${view}`;

  const [meta, setMeta] = useState<ViewSummary | null>(null);
  const [spec, setSpec] = useState<ViewSpec | null>(null);
  const [saved, setSaved] = useState("");
  const [data, setData] = useState<DataMap>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "publish" | null>(null);
  const [showRevisions, setShowRevisions] = useState(false);
  const [addMenu, setAddMenu] = useState(false);

  const dirty = !!spec && fingerprint(spec) !== saved;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  const loadData = useCallback(async (s: ViewSpec) => {
    const runnable = s.elements.filter((e) => e.sql.trim() && e.type !== "text");
    setData(Object.fromEntries(runnable.map((e) => [e.id, { status: "loading" as const }])));
    await Promise.all(runnable.map(async (e) => {
      try {
        const d = await get<ElementData>(`${viewPath}/data/${e.id}?draft=true`);
        setData((m) => ({ ...m, [e.id]: { status: "ok", data: d } }));
      } catch (err) {
        setData((m) => ({ ...m, [e.id]: { status: "error", message: errMsg(err) } }));
      }
    }));
  }, [viewPath]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const m = await get<ViewSummary>(viewPath);
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
      setSelectedId(null);
      void loadData(s);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [viewPath, loadData]);

  useEffect(() => { void load(); }, [load]);

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
    setAddMenu(false);
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

  async function publish() {
    setError(null); setNotice(null); setBusy("publish");
    try {
      const r = await post<{ published_revision: number }>(`${viewPath}/publish`);
      setNotice(`Published revision ${r.published_revision}`);
      setMeta(await get<ViewSummary>(viewPath));
    } catch (e) { setError(`Publish refused: ${errMsg(e)}`); } finally { setBusy(null); }
  }

  const selected = useMemo(() => spec?.elements.find((e) => e.id === selectedId) ?? null, [spec, selectedId]);
  const selectedLayout = spec?.layout.find((l) => l.element_id === selectedId) ?? null;
  const canPublish = !!meta && meta.draft_revision !== null && meta.has_unpublished_changes && !dirty && !busy;

  if (!spec || !meta) return <div className="p-8">{error ? <ErrorBox error={error} /> : <span className="text-sm text-slate-500">Loading…</span>}</div>;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-5 py-3">
        <h1 className="text-lg font-semibold">{meta.name}</h1>
        {viewStatus(meta)}
        {dirty && <Badge tone="blue">Unsaved edits</Badge>}
        <div className="ml-auto flex items-center gap-2">
          <Btn variant="secondary" onClick={() => setShowRevisions(true)}>Revisions</Btn>
          <Btn variant="secondary" onClick={save} disabled={!!busy || !dirty}>{busy === "save" ? "Saving…" : "Save"}</Btn>
          <Btn onClick={publish} disabled={!canPublish} title={dirty ? "Save your edits first" : !meta.has_unpublished_changes ? "Nothing to publish" : ""}>
            {busy === "publish" ? "Publishing…" : "Publish"}
          </Btn>
        </div>
      </header>
      {(error || notice) && (
        <div className="px-5 pt-3">
          <ErrorBox error={error} />
          {notice && <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">{notice}</div>}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="relative flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-auto p-5 pb-24">
            <div className="mb-3 flex items-center gap-2">
              <div className="relative">
                <Btn variant="secondary" onClick={() => setAddMenu(!addMenu)}>+ Add element</Btn>
                {addMenu && (
                  <div className="absolute left-0 z-20 mt-1 w-36 rounded-md border border-slate-200 bg-white py-1 shadow-lg">
                    {(["chart", "table", "kpi"] as const).map((t) => (
                      <button key={t} onClick={() => addElement(t)} className="block w-full px-3 py-1.5 text-left text-sm capitalize hover:bg-slate-100">{t}</button>
                    ))}
                  </div>
                )}
              </div>
              <span className="text-xs text-slate-400">Click an element to edit it.</span>
            </div>
            {spec.elements.length === 0 ? (
              <div className="rounded-lg border-2 border-dashed border-slate-300 p-16 text-center text-slate-400">
                Empty view. Use “+ Add element” to start, or wait for AI chat in the next phase.
              </div>
            ) : (
              <SpecRenderer spec={spec} data={data} selectedId={selectedId} onSelect={setSelectedId} />
            )}
          </div>
          <div className="absolute inset-x-0 bottom-0 border-t border-slate-200 bg-white/95 p-3">
            <input disabled placeholder="AI chat arrives in the next phase"
              className="w-full cursor-not-allowed rounded-full border border-slate-300 bg-slate-100 px-4 py-2 text-sm text-slate-400" />
          </div>
        </div>

        {selected && (
          <ElementPanel
            key={selected.id}
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
      </div>

      {showRevisions && (
        <RevisionsDrawer
          viewPath={viewPath}
          dirty={dirty}
          onClose={() => setShowRevisions(false)}
          onRestored={async () => { setShowRevisions(false); await load(); setNotice("Restored as a new draft revision"); }}
        />
      )}
    </div>
  );
}
