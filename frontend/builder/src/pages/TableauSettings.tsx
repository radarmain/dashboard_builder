import { useEffect, useState } from "react";
import { Alert, Autocomplete, Box, TextField, Typography } from "@mui/material";
import { get, put, type ImportPlan, type PlanResponse, type PlanSheet, type TableauLink } from "../api";
import { Badge, Btn, Card, ErrorBox, Loading, errMsg, monoSx } from "../ui";

interface WorkbookOption { id: string; name: string; project: string | null }

export const STATUS_TONE = { supported: "green", approximate: "amber", unsupported: "gray" } as const;

function SheetRow({ s }: { s: PlanSheet }) {
  const cond = s.filters.map((f) => `${f.field}${f.default ? `: ${f.default}` : ""}`);
  return (
    <Box sx={{ py: 1, borderTop: 1, borderColor: "divider", display: "grid", gap: 0.5 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <Typography variant="subtitle2">{s.name}</Typography>
        <Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge>
        <Typography variant="caption" color="text.secondary">{s.chart_type}{s.dual_axis ? ", dual axis" : ""}</Typography>
      </Box>
      {s.status_reason && <Typography variant="caption" color="text.secondary">{s.status_reason}</Typography>}
      {!!cond.length && <Typography variant="caption" color="text.secondary">Filters: {cond.join("; ")}</Typography>}
    </Box>
  );
}

function PlanView({ plan }: { plan: ImportPlan }) {
  const tables = [...new Set(plan.datasources.flatMap((d) => d.tables))];
  return (
    <Box sx={{ display: "grid", gap: 1.5 }}>
      <Typography variant="body2">
        Dashboards: {plan.dashboards.map((d) => `${d.name} (${d.sheets.length} sheets)`).join(", ") || "none"}
      </Typography>
      <Typography variant="body2" component="div">
        Tables used: {tables.length ? tables.map((t) => <Box key={t} component="span" sx={{ ...monoSx, mr: 1 }}>{t}</Box>) : "none found"}
      </Typography>
      {!!plan.calculated_fields.length && (
        <Box>
          <Typography variant="subtitle2">Calculated fields</Typography>
          {plan.calculated_fields.map((c) => (
            <Typography key={c.name} variant="caption" component="div" sx={monoSx}>{c.name} ({c.kind}) = {c.formula}</Typography>
          ))}
        </Box>
      )}
      <Box>
        <Typography variant="subtitle2">Worksheets</Typography>
        {plan.worksheets.map((s) => <SheetRow key={s.name} s={s} />)}
      </Box>
    </Box>
  );
}

/** Settings > Tableau: pick the workbook this project mirrors, then preview the sanitized import plan. */
export function TableauSettings({ client, project, link, onLinked }: {
  client: string; project: string; link: TableauLink | null; onLinked: (l: TableauLink) => void;
}) {
  const base = `/clients/${client}/projects/${project}`;
  const [options, setOptions] = useState<WorkbookOption[] | null>(null);
  const [picked, setPicked] = useState<WorkbookOption | null>(null);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | "plan" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadOptions = async () => {
    if (options || busy) return;
    setBusy("load"); setError(null);
    try { setOptions(await get<WorkbookOption[]>("/tableau/workbooks")); } catch (e) { setError(errMsg(e)); } finally { setBusy(null); }
  };
  useEffect(() => { if (!link) void loadOptions(); }, [link]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    if (!picked) return;
    setBusy("save"); setError(null);
    try {
      const l = await put<TableauLink>(`${base}/tableau-link`, { workbook_id: picked.id, workbook_name: picked.name });
      onLinked(l); setPicked(null); setPlan(null);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(null); }
  }
  async function loadPlan() {
    setBusy("plan"); setError(null);
    try { setPlan(await get<PlanResponse>(`${base}/tableau-import/plan`)); } catch (e) { setError(errMsg(e)); } finally { setBusy(null); }
  }

  return (
    <Card sx={{ mt: 3, display: "grid", gap: 2 }}>
      <Typography variant="h6" component="h2">Tableau</Typography>
      <Alert severity="info">Read-only. Nothing is published automatically: an import only fills an unsaved draft in a new view.</Alert>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {link && <Typography>Linked workbook: <strong>{link.workbook_name}</strong></Typography>}
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
        <Autocomplete
          sx={{ flex: 1 }} options={options ?? []} value={picked} loading={busy === "load"}
          onOpen={() => void loadOptions()} onChange={(_, v) => setPicked(v)}
          getOptionLabel={(o) => o.name} isOptionEqualToValue={(a, b) => a.id === b.id}
          groupBy={(o) => o.project ?? ""}
          filterOptions={(opts, st) => opts.filter((o) => `${o.project} ${o.name}`.toLowerCase().includes(st.inputValue.toLowerCase())).slice(0, 60)}
          renderInput={(p) => <TextField {...p} label={link ? "Change workbook" : "Search Tableau workbooks"} />}
        />
        <Btn onClick={save} disabled={!picked || !!busy} sx={{ mt: 1 }}>{busy === "save" ? "Saving…" : "Save link"}</Btn>
      </Box>
      {link && <Box><Btn variant="secondary" onClick={loadPlan} disabled={!!busy}>{busy === "plan" ? "Reading workbook…" : "Show import plan"}</Btn></Box>}
      {busy === "plan" && <Loading />}
      {plan && <PlanView plan={plan.plan} />}
    </Card>
  );
}
