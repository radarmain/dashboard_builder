import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Tab, Tabs, Typography } from "@mui/material";
import { get, post, type Project, type ViewSummary } from "../api";
import { Btn, ErrorBox, Loading, PageHeader, errMsg, monoSx, viewStatus } from "../ui";
import { ClientPreviewButton, PREVIEW_BASE } from "../Preview";
import { LinkCard, NameSlugForm } from "./Clients";
import { Settings } from "./Settings";

type TabId = "settings" | "builder";

export function ProjectPage() {
  const { client = "", project = "" } = useParams();
  const [info, setInfo] = useState<Project | null>(null);
  const [tab, setTab] = useState<TabId | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Project>(`/clients/${client}/projects/${project}`)
      .then((p) => { setInfo(p); setTab((t) => t ?? (p.config?.tables.length ? "builder" : "settings")); })
      .catch((e) => setError(errMsg(e)));
  }, [client, project]);

  return (
    <Box sx={{ p: 4 }}>
      <PageHeader title={info?.name ?? project}
        subtitle={<Typography component="span" variant="caption" sx={monoSx}>{client}.radar-analytics.com/{project}</Typography>}
        action={<ClientPreviewButton to={`${PREVIEW_BASE}/c/${client}/p/${project}`} />} />
      <ErrorBox error={error} />
      <Tabs value={tab ?? false} onChange={(_, v: TabId) => setTab(v)} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
        <Tab value="settings" label="Settings" />
        <Tab value="builder" label="Builder" />
      </Tabs>
      {tab === "settings" && (
        <Settings client={client} project={project} initial={info?.config ?? null}
          onSaved={(cfg) => setInfo((p) => (p ? { ...p, config: cfg } : p))} />
      )}
      {tab === "builder" && <ViewList client={client} project={project} hasTables={!!info?.config?.tables.length} goSettings={() => setTab("settings")} />}
    </Box>
  );
}

function ViewList({ client, project, hasTables, goSettings }: { client: string; project: string; hasTables: boolean; goSettings: () => void }) {
  const [views, setViews] = useState<ViewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    get<ViewSummary[]>(`/clients/${client}/projects/${project}/views`).then(setViews).catch((e) => setError(errMsg(e)));
  }, [client, project]);

  return (
    <Box sx={{ display: "grid", gap: 2 }}>
      {!hasTables && (
        <Alert severity="warning" action={<Btn variant="ghost" size="small" color="inherit" onClick={goSettings}>Open Settings</Btn>}>
          No BigQuery tables are connected yet, so queries will be rejected.
        </Alert>
      )}
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography variant="h6" component="h2">Views</Typography>
        <Btn onClick={() => setAdding(true)}>Add view</Btn>
      </Box>
      {adding && (
        <NameSlugForm label="View" withDescription onCancel={() => setAdding(false)}
          onSubmit={async (name, slug, description) => { await post(`/clients/${client}/projects/${project}/views`, { name, slug, ...(description ? { description } : {}) }); nav(`/c/${client}/p/${project}/v/${slug}`); }} />
      )}
      <ErrorBox error={error} />
      {views === null && !error && <Loading />}
      {views?.length === 0 && <Typography color="text.secondary">No views yet.</Typography>}
      {views?.map((v) => (
        <LinkCard key={v.slug} to={`/c/${client}/p/${project}/v/${v.slug}`} title={v.name} subtitle={v.slug} description={v.description} trailing={viewStatus(v)} />
      ))}
    </Box>
  );
}
