import { useEffect, useMemo, useState } from "react";
import { get, put, type ProjectConfig, type SchemaSnapshot } from "../api";
import { Btn, Card, ErrorBox, Field, errMsg, inputCls } from "../ui";

// In-memory caches (the BigQuery project list is ~93 entries and rarely changes).
const cache = {
  projects: null as string[] | null,
  datasets: new Map<string, string[]>(),
  tables: new Map<string, { table_id: string; type: string }[]>(),
};

async function loadProjects() {
  return (cache.projects ??= await get<string[]>("/gbq/projects"));
}
async function loadDatasets(bq: string) {
  if (!cache.datasets.has(bq)) cache.datasets.set(bq, await get<string[]>(`/gbq/projects/${bq}/datasets`));
  return cache.datasets.get(bq)!;
}
async function loadTables(bq: string, d: string) {
  const k = `${bq}.${d}`;
  if (!cache.tables.has(k)) cache.tables.set(k, await get(`/gbq/projects/${bq}/datasets/${d}/tables`));
  return cache.tables.get(k)!;
}

function SearchSelect({ value, options, onChange, placeholder }: {
  value: string; options: string[]; onChange: (v: string) => void; placeholder: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const shown = useMemo(() => options.filter((o) => o.toLowerCase().includes(q.toLowerCase())).slice(0, 50), [options, q]);
  return (
    <div className="relative">
      <input className={inputCls} placeholder={placeholder} value={open ? q : value}
        onFocus={() => { setOpen(true); setQ(""); }} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => setQ(e.target.value)} />
      {open && (
        <ul className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {shown.length === 0 && <li className="px-3 py-2 text-sm text-slate-400">No matches</li>}
          {shown.map((o) => (
            <li key={o} onMouseDown={() => { onChange(o); setOpen(false); }}
              className={`cursor-pointer px-3 py-1.5 text-sm hover:bg-blue-50 ${o === value ? "font-medium text-blue-700" : ""}`}>{o}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Settings({ client, project, initial, onSaved }: {
  client: string; project: string; initial: ProjectConfig | null; onSaved: (c: ProjectConfig) => void;
}) {
  const base = `/clients/${client}/projects/${project}`;
  const [bq, setBq] = useState(initial?.bq_project ?? "");
  const [bqProjects, setBqProjects] = useState<string[]>([]);
  const [datasets, setDatasets] = useState<string[]>([]);
  const [dataset, setDataset] = useState("");
  const [tables, setTables] = useState<{ table_id: string; type: string }[]>([]);
  const [tableQ, setTableQ] = useState("");
  const [selected, setSelected] = useState<string[]>(initial?.tables ?? []);
  const [gb, setGb] = useState("");
  const [defaultGb, setDefaultGb] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string[]>(initial?.tables ?? []);
  const [loadingList, setLoadingList] = useState(false);

  useEffect(() => {
    loadProjects().then(setBqProjects).catch((e) => setError(errMsg(e)));
    get<ProjectConfig>(`${base}/settings`).then((c) => {
      setDefaultGb((c.default_max_bytes_billed ?? 0) / 1e9);
      if (c.max_bytes_billed) setGb(String(c.max_bytes_billed / 1e9));
    }).catch((e) => setError(errMsg(e)));
  }, [base]);

  useEffect(() => {
    setDatasets([]); setDataset(""); setTables([]);
    if (!bq) return;
    loadDatasets(bq).then(setDatasets).catch((e) => setError(errMsg(e)));
  }, [bq]);

  useEffect(() => {
    setTables([]);
    if (!bq || !dataset) return;
    setLoadingList(true);
    loadTables(bq, dataset).then(setTables).catch((e) => setError(errMsg(e))).finally(() => setLoadingList(false));
  }, [bq, dataset]);

  const fqn = (t: string) => `${bq}.${dataset}.${t}`;
  const toggle = (name: string) => setSelected((s) => (s.includes(name) ? s.filter((x) => x !== name) : [...s, name]));
  const shownTables = tables.filter((t) => t.table_id.toLowerCase().includes(tableQ.toLowerCase()));
  const dirty = JSON.stringify([...selected].sort()) !== JSON.stringify([...saved].sort()) || gb !== (initial?.max_bytes_billed ? String(initial.max_bytes_billed / 1e9) : "");

  async function save() {
    setError(null);
    setSaving(true);
    try {
      const body: Record<string, unknown> = { bq_project: bq, tables: selected };
      if (gb.trim()) {
        const n = Number(gb);
        if (!(n > 0)) throw new Error("Max bytes billed must be a positive number of GB");
        body.max_bytes_billed = Math.round(n * 1e9);
      }
      const cfg = await put<ProjectConfig>(`${base}/settings`, body);
      setSaved(cfg.tables);
      onSaved(cfg);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <Card className="space-y-4">
        <h2 className="text-lg font-medium">BigQuery connection</h2>
        <Field label="BigQuery project" hint={bqProjects.length ? `${bqProjects.length} projects visible. Type to filter.` : "Loading projects…"}>
          <SearchSelect value={bq} options={bqProjects} placeholder="Search projects…"
            onChange={(v) => { if (v !== bq) { setBq(v); setSelected([]); } }} />
        </Field>
        {bq && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="Dataset" hint="Pick a dataset to browse its tables. Selections persist across datasets.">
              <SearchSelect value={dataset} options={datasets} placeholder="Search datasets…" onChange={setDataset} />
            </Field>
            <Field label="Tables">
              <input className={inputCls} placeholder="Filter tables…" value={tableQ} onChange={(e) => setTableQ(e.target.value)} disabled={!dataset} />
            </Field>
          </div>
        )}
        {dataset && (
          <div className="max-h-64 overflow-auto rounded-md border border-slate-200">
            {loadingList && <div className="p-3 text-sm text-slate-400">Loading tables…</div>}
            {!loadingList && shownTables.length === 0 && <div className="p-3 text-sm text-slate-400">No tables</div>}
            {shownTables.map((t) => (
              <label key={t.table_id} className="flex cursor-pointer items-center gap-2 border-b border-slate-100 px-3 py-1.5 text-sm hover:bg-slate-50">
                <input type="checkbox" checked={selected.includes(fqn(t.table_id))} onChange={() => toggle(fqn(t.table_id))} />
                <span className="font-mono">{t.table_id}</span>
                <span className="text-xs text-slate-400">{t.type}</span>
              </label>
            ))}
          </div>
        )}
        <div>
          <div className="mb-1 text-sm font-medium text-slate-700">Connected tables ({selected.length})</div>
          {selected.length === 0 && <div className="text-sm text-slate-400">None selected</div>}
          <div className="flex flex-wrap gap-2">
            {selected.map((s) => (
              <span key={s} className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-1 font-mono text-xs text-blue-800">
                {s.split(".").slice(1).join(".")}
                <button onClick={() => toggle(s)} className="text-blue-400 hover:text-blue-700" title="Remove">×</button>
              </span>
            ))}
          </div>
        </div>
        <Field label="Max bytes billed per query (GB)" hint={`Leave blank to use the default${defaultGb ? ` (${defaultGb} GB)` : ""}. Queries estimated above the cap are rejected.`}>
          <input className={`${inputCls} max-w-[10rem]`} inputMode="decimal" placeholder={defaultGb ? String(defaultGb) : ""} value={gb} onChange={(e) => setGb(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
        <div className="flex items-center gap-3">
          <Btn onClick={save} disabled={saving || !bq || selected.length === 0 || !dirty}>{saving ? "Saving and snapshotting…" : "Save settings"}</Btn>
          {!dirty && saved.length > 0 && <span className="text-sm text-green-700">Saved</span>}
        </div>
      </Card>

      {saved.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-medium">Table schemas</h2>
          <p className="text-sm text-slate-500">Descriptions help the AI write accurate SQL.</p>
          {saved.map((t) => <SchemaCard key={t} base={base} fqn={t} />)}
        </div>
      )}
    </div>
  );
}

function SchemaCard({ base, fqn }: { base: string; fqn: string }) {
  const [, dataset, table] = fqn.split(".");
  const path = `${base}/schemas/${dataset}.${table}`;
  const [snap, setSnap] = useState<SchemaSnapshot | null>(null);
  const [desc, setDesc] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    get<SchemaSnapshot>(path).then((s) => { setSnap(s); setDesc(s.descriptions ?? {}); }).catch((e) => setError(errMsg(e)));
  }, [path]);

  async function save() {
    setError(null); setMsg(null);
    try {
      const s = await put<SchemaSnapshot>(`${path}/descriptions`, desc);
      setSnap(s); setMsg("Saved");
    } catch (e) { setError(errMsg(e)); }
  }

  return (
    <Card>
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen(!open)}>
        <span className="font-mono text-sm font-medium">{dataset}.{table}</span>
        <span className="text-xs text-slate-500">
          {snap ? `${snap.columns.length} columns · ${snap.num_rows?.toLocaleString() ?? "?"} rows` : "loading…"} {open ? "▲" : "▼"}
        </span>
      </button>
      <ErrorBox error={error} />
      {open && snap && (
        <div className="mt-3 space-y-3">
          <div className="max-h-96 overflow-auto rounded border border-slate-200">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-slate-100 text-left text-slate-600">
                <tr><th className="px-3 py-1.5">Column</th><th className="px-3 py-1.5">Type</th><th className="w-2/3 px-3 py-1.5">Description</th></tr>
              </thead>
              <tbody>
                {snap.columns.map((c) => (
                  <tr key={c.name} className="border-t border-slate-100">
                    <td className="whitespace-nowrap px-3 py-1 font-mono">{c.name}</td>
                    <td className="whitespace-nowrap px-3 py-1 text-slate-500">{c.type}{c.mode === "REPEATED" ? "[]" : ""}</td>
                    <td className="px-3 py-1">
                      <input className={inputCls} value={desc[c.name] ?? ""} placeholder={c.bq_description || "Add a description"}
                        onChange={(e) => setDesc((d) => ({ ...d, [c.name]: e.target.value }))} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-3">
            <Btn onClick={save}>Save descriptions</Btn>
            {msg && <span className="text-sm text-green-700">{msg}</span>}
          </div>
        </div>
      )}
    </Card>
  );
}
