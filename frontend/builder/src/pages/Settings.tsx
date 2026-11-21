import { useEffect, useState } from "react";
import {
  Autocomplete, Box, Checkbox, Chip, Collapse, FormControlLabel, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, Typography,
} from "@mui/material";
import { get, put, type ProjectConfig, type SchemaSnapshot } from "../api";
import { MONO } from "@rdb/core";
import { Btn, Card, ErrorBox, Loading, errMsg, monoSx } from "../ui";

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

function SearchSelect({ value, options, onChange, label, helperText, disabled }: {
  value: string; options: string[]; onChange: (v: string) => void; label: string; helperText?: string; disabled?: boolean;
}) {
  return (
    <Autocomplete
      options={options} value={(value || null) as unknown as string} disableClearable disabled={disabled}
      onChange={(_, v) => onChange(v)}
      slotProps={{ listbox: { sx: { maxHeight: 256 } } }}
      filterOptions={(opts, st) => opts.filter((o) => o.toLowerCase().includes(st.inputValue.toLowerCase())).slice(0, 50)}
      renderInput={(params) => <TextField {...params} label={label} helperText={helperText} />}
    />
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
    <Box sx={{ maxWidth: 896, display: "grid", gap: 3 }}>
      <Card sx={{ display: "grid", gap: 2.5 }}>
        <Typography variant="h6" component="h2">BigQuery connection</Typography>
        <SearchSelect label="BigQuery project" value={bq} options={bqProjects}
          helperText={bqProjects.length ? `${bqProjects.length} projects visible. Type to filter.` : "Loading projects…"}
          onChange={(v) => { if (v !== bq) { setBq(v); setSelected([]); } }} />
        {bq && (
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
            <SearchSelect label="Dataset" value={dataset} options={datasets} onChange={setDataset}
              helperText="Pick a dataset to browse its tables. Selections persist across datasets." />
            <TextField label="Filter tables" value={tableQ} onChange={(e) => setTableQ(e.target.value)} disabled={!dataset} />
          </Box>
        )}
        {dataset && (
          <Paper variant="outlined" sx={{ maxHeight: 256, overflow: "auto", borderColor: "divider" }}>
            {loadingList && <Box sx={{ px: 2 }}><Loading label="Loading tables…" /></Box>}
            {!loadingList && shownTables.length === 0 && <Typography variant="body2" sx={{ p: 2, color: "text.secondary" }}>No tables</Typography>}
            {shownTables.map((t) => (
              <FormControlLabel key={t.table_id} sx={{ display: "flex", m: 0, px: 1.5, py: 0.25, borderBottom: 1, borderColor: "divider", "&:hover": { bgcolor: "action.hover" } }}
                control={<Checkbox checked={selected.includes(fqn(t.table_id))} onChange={() => toggle(fqn(t.table_id))} />}
                label={<Box sx={{ display: "flex", gap: 1, alignItems: "baseline" }}>
                  <Typography variant="body2" sx={monoSx}>{t.table_id}</Typography>
                  <Typography variant="caption" color="text.secondary">{t.type}</Typography>
                </Box>} />
            ))}
          </Paper>
        )}
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Connected tables ({selected.length})</Typography>
          {selected.length === 0 && <Typography variant="body2" color="text.secondary">None selected</Typography>}
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
            {selected.map((s) => (
              <Chip key={s} label={s.split(".").slice(1).join(".")} onDelete={() => toggle(s)} sx={{ fontFamily: MONO, bgcolor: (t) => t.palette.tokens.primaryContainer, color: (t) => t.palette.tokens.onPrimaryContainer }} />
            ))}
          </Box>
        </Box>
        <TextField label="Max bytes billed per query (GB)" value={gb} onChange={(e) => setGb(e.target.value)} sx={{ maxWidth: 280 }}
          placeholder={defaultGb ? String(defaultGb) : ""} slotProps={{ htmlInput: { inputMode: "decimal" } }}
          helperText={`Leave blank to use the default${defaultGb ? ` (${defaultGb} GB)` : ""}. Queries estimated above the cap are rejected.`} />
        <ErrorBox error={error} />
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Btn onClick={save} disabled={saving || !bq || selected.length === 0 || !dirty}>{saving ? "Saving and snapshotting…" : "Save settings"}</Btn>
          {!dirty && saved.length > 0 && <Typography variant="body2" sx={{ color: "success.main" }}>Saved</Typography>}
        </Box>
      </Card>

      {saved.length > 0 && (
        <Box sx={{ display: "grid", gap: 1.5 }}>
          <Box>
            <Typography variant="h6" component="h2">Table schemas</Typography>
            <Typography variant="body2" color="text.secondary">Descriptions help the AI write accurate SQL.</Typography>
          </Box>
          {saved.map((t) => <SchemaCard key={t} base={base} fqn={t} />)}
        </Box>
      )}
    </Box>
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
    <Card sx={{ p: 0 }}>
      <Box component="button" onClick={() => setOpen(!open)} aria-expanded={open}
        sx={{ all: "unset", boxSizing: "border-box", cursor: "pointer", display: "flex", width: "100%", alignItems: "center", justifyContent: "space-between", p: 2.5, borderRadius: "12px",
          "&:hover": { bgcolor: "action.hover" } }}>
        <Typography variant="subtitle2" sx={monoSx}>{dataset}.{table}</Typography>
        <Typography variant="caption" color="text.secondary">
          {snap ? `${snap.columns.length} columns · ${snap.num_rows?.toLocaleString() ?? "?"} rows` : "loading…"} {open ? "▲" : "▼"}
        </Typography>
      </Box>
      {error && <Box sx={{ px: 2.5, pb: 2 }}><ErrorBox error={error} /></Box>}
      <Collapse in={open && !!snap} unmountOnExit>
        {snap && (
          <Box sx={{ display: "grid", gap: 2, px: 2.5, pb: 2.5 }}>
            <TableContainer sx={{ maxHeight: 384, border: 1, borderColor: "divider", borderRadius: "12px" }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow><TableCell>Column</TableCell><TableCell>Type</TableCell><TableCell sx={{ width: "60%" }}>Description</TableCell></TableRow>
                </TableHead>
                <TableBody>
                  {snap.columns.map((c) => (
                    <TableRow key={c.name} hover>
                      <TableCell sx={{ whiteSpace: "nowrap", ...monoSx }}>{c.name}</TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap", color: "text.secondary" }}>{c.type}{c.mode === "REPEATED" ? "[]" : ""}</TableCell>
                      <TableCell>
                        <TextField value={desc[c.name] ?? ""} placeholder={c.bq_description || "Add a description"} slotProps={{ htmlInput: { "aria-label": `Description for ${c.name}` } }}
                          onChange={(e) => setDesc((d) => ({ ...d, [c.name]: e.target.value }))} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              <Btn onClick={save}>Save descriptions</Btn>
              {msg && <Typography variant="body2" sx={{ color: "success.main" }}>{msg}</Typography>}
            </Box>
          </Box>
        )}
      </Collapse>
    </Card>
  );
}
