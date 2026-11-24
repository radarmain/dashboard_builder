import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Tab, Tabs, Typography } from "@mui/material";
import { ArchivedBanner, FavStar, LifecycleMenu } from "../Lifecycle";
import { invalidateAll, shown, useNavCache } from "../navTree";
import { get, post, type Project, type TableauLink, type ViewSummary } from "../api";
import { Btn, ErrorBox, Loading, PageHeader, errMsg, monoSx, slugify, viewStatus } from "../ui";
import { ClientPreviewButton, PREVIEW_BASE } from "../Preview";
import { LinkCard, NameSlugForm } from "./Clients";
import { Settings } from "./Settings";
import { TableauSettings } from "./TableauSettings";
import { ProjectGeneral } from "./ProjectGeneral";

type TabId = "settings" | "builder";

export function ProjectPage() {
  const { client = "", project = "" } = useParams();
  const navigate = useNavigate();
  const [info, setInfo] = useState<Project | null>(null);
  const [tab, setTab] = useState<TabId | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useNavCache();

  useEffect(() => {
    get<Project>(`/clients/${client}/projects/${project}`)
      .then((p) => {
        if (p.slug !== project) { navigate(`/c/${client}/p/${p.slug}`, { replace: true }); return; } // old link -> canonical
        setInfo(p); setTab((t) => t ?? (p.config?.tables.length ? "builder" : "settings"));
      })
      .catch((e) => setError(errMsg(e)));
  }, [client, project, navigate, reload]);
  const archived = info?.effective_lifecycle === "archived";
  useEffect(() => { if (info?.name) document.title = `${info.name} · RADaR Dashboard Builder`; }, [info?.name]);

  return (
    <Box sx={{ p: 4 }}>
      <PageHeader title={<>{info?.name ?? project} <FavStar always size={18} item={{ kind: "project", client, project }} name={info?.name ?? project} path={client} /></>}
        subtitle={<Typography component="span" variant="caption" sx={monoSx}>{client}.radar-analytics.com/{project}</Typography>}
        action={
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            {!archived && <ClientPreviewButton to={`${PREVIEW_BASE}/c/${client}/p/${project}`} />}
            {info && (
              <LifecycleMenu item={{ kind: "project", client, project }} name={info.name} lifecycle={info.lifecycle} publishedViews
                onChanged={() => { invalidateAll(); setReload((n) => n + 1); }}
                onDeleted={() => { invalidateAll(); navigate(`/c/${client}`); }} />
            )}
          </Box>
        } />
      {archived && <ArchivedBanner kind="project" />}
      <ErrorBox error={error} />
      <Tabs value={tab ?? false} onChange={(_, v: TabId) => setTab(v)} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
        <Tab value="settings" label="Settings" />
        <Tab value="builder" label="Builder" />
      </Tabs>
      {tab === "settings" && info && (
        <ProjectGeneral client={client} project={project} name={info.name}
          onRenamed={(p) => { setInfo((cur) => (cur ? { ...cur, name: p.name, slug: p.slug } : cur)); if (p.slug !== project) navigate(`/c/${client}/p/${p.slug}`, { replace: true }); }} />
      )}
      {tab === "settings" && (
        <Settings client={client} project={project} initial={info?.config ?? null}
          onSaved={(cfg) => setInfo((p) => (p ? { ...p, config: cfg } : p))} />
      )}
      {tab === "settings" && info?.config && (
        <TableauSettings client={client} project={project} link={info.config.tableau_link ?? null}
          onLinked={(l) => setInfo((p) => (p && p.config ? { ...p, config: { ...p.config, tableau_link: l } } : p))} />
      )}
      {tab === "builder" && <ViewList archived={archived} reload={reload} client={client} project={project} hasTables={!!info?.config?.tables.length} tableau={info?.config?.tableau_link ?? null} goSettings={() => setTab("settings")} />}
    </Box>
  );
}

function ViewList({ client, project, hasTables, tableau, goSettings, archived, reload }: { archived: boolean; reload: number; client: string; project: string; hasTables: boolean; tableau: TableauLink | null; goSettings: () => void }) {
  const [views, setViews] = useState<ViewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();

  /** New view "Imported: <workbook>"; the editor then runs the import into its local draft. */
  async function startImport() {
    if (!tableau) return;
    const name = `Imported: ${tableau.workbook_name}`.slice(0, 120);
    const slug = slugify(name) || "imported";
    const taken = new Set(views?.map((v) => v.slug));
    const final = taken.has(slug) ? `${slug.slice(0, 56)}-${Math.random().toString(36).slice(2, 6)}` : slug;
    try {
      await post(`/clients/${client}/projects/${project}/views`, { name, slug: final });
      nav(`/c/${client}/p/${project}/v/${final}?import=1`);
    } catch (e) { setError(errMsg(e)); }
  }

  useEffect(() => {
    get<ViewSummary[]>(`/clients/${client}/projects/${project}/views?include_archived=true`).then(setViews).catch((e) => setError(errMsg(e)));
  }, [client, project, reload]);

  return (
    <Box sx={{ display: "grid", gap: 2 }}>
      {!hasTables && (
        <Alert severity="warning" action={<Btn variant="ghost" size="small" color="inherit" onClick={goSettings}>Open Settings</Btn>}>
          No BigQuery tables are connected yet, so queries will be rejected.
        </Alert>
      )}
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography variant="h6" component="h2">Views</Typography>
        <Box sx={{ display: "flex", gap: 1 }}>
          {tableau && hasTables && !archived && (
            <Btn variant="secondary" onClick={() => void startImport()} title="Creates a new view and fills an unsaved draft. Nothing is published.">
              Import from Tableau
            </Btn>
          )}
          <Btn onClick={() => setAdding(true)} disabled={archived}>Add view</Btn>
        </Box>
      </Box>
      {adding && (
        <NameSlugForm label="View" withDescription onCancel={() => setAdding(false)}
          onSubmit={async (name, slug, description) => { await post(`/clients/${client}/projects/${project}/views`, { name, slug, ...(description ? { description } : {}) }); nav(`/c/${client}/p/${project}/v/${slug}`); }} />
      )}
      <ErrorBox error={error} />
      {views === null && !error && <Loading />}
      {views && views.filter(shown).length === 0 && <Typography color="text.secondary">No views yet.</Typography>}
      {views?.filter(shown).map((v) => (
        <LinkCard key={v.slug} archived={v.effective_lifecycle === "archived"} to={`/c/${client}/p/${project}/v/${v.slug}`} title={v.name} subtitle={v.slug} description={v.description} trailing={viewStatus(v)} />
      ))}
    </Box>
  );
}
