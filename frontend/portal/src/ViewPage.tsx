import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Typography } from "@mui/material";
import { SpecRenderer, type DataMap, type ViewElement, type ViewSpec } from "@rdb/shared";
import { NotFound } from "./Pages";
import { PortalBreadcrumbs, useDocTitle, useReportClient } from "./PortalShell";
import { fmtDate } from "./ViewCard";
import { portalApi, rememberClient, useFetched, type PortalViewData } from "./api";
import { usePortal, usePortalParams } from "./context";

const COLLAPSE_BELOW = 900;

/** The portal view has no SQL and no revision info; fill the rest of the shared spec shape. */
function toSpec(v: PortalViewData): ViewSpec {
  const elements: ViewElement[] = v.elements.map((e) => ({
    id: e.id, type: e.type, title: e.title, sql: "", format: e.format ?? {}, cache_ttl_seconds: 0,
    chart: e.chart,
  }));
  return { schema_version: 1, view: { name: v.name, slug: "" }, revision: 0, updated_at: "", updated_by: "", layout: v.layout, elements };
}

/** Published dashboard on the shared 12-column SpecRenderer; data is fetched per element. */
export function ViewPage() {
  const { client, project, view } = usePortalParams();
  const { links } = usePortal();
  const f = useFetched(`view:${client}/${project}/${view}`, () => portalApi.view(client, project, view));
  const v = f.data;
  const navigate = useNavigate();
  useEffect(() => { // reached via an old (aliased) link: swap in the canonical URL
    if (v && (v.project.slug !== project || v.slug !== view)) navigate(links.view(client, v.project.slug, v.slug), { replace: true });
  }, [v, client, project, view, links, navigate]);
  if (v) rememberClient(v.client);
  useReportClient(client, v?.client.name);
  useDocTitle(v?.name, v?.client.name);

  const spec = useMemo(() => (v ? toSpec(v) : null), [v]);
  const [data, setData] = useState<DataMap>({});

  useEffect(() => {
    if (!v) return;
    let live = true;
    const runnable = v.elements.filter((e) => e.type !== "text");
    setData(Object.fromEntries(runnable.map((e) => [e.id, { status: "loading" as const }])));
    for (const e of runnable) {
      portalApi.data(client, project, view, e.id).then(
        (d) => live && setData((m) => ({ ...m, [e.id]: { status: "ok", data: d } })),
        () => live && setData((m) => ({ ...m, [e.id]: { status: "error", message: "This item couldn't be loaded right now." } })),
      );
    }
    return () => { live = false; };
  }, [v, client, project, view]);

  if (f.notFound) return <NotFound title="Dashboard not found" body="It may not be published, or it isn't available to you." />;
  if (f.error) return <NotFound title="Something went wrong" body={f.error} />;

  const updated = fmtDate(v?.published_at ?? null);
  return (
    <>
      <PortalBreadcrumbs
        items={[
          { label: v?.client.name ?? "…", to: links.client(client) },
          { label: v?.project.name ?? "…", to: links.project(client, project) },
          { label: v?.name ?? "…" },
        ]}
      />
      <Typography variant="h4" component="h1">{v?.name ?? " "}</Typography>
      {v?.description && <Typography color="text.secondary" sx={{ mt: 0.5, maxWidth: 820 }}>{v.description}</Typography>}
      {updated && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>Updated {updated}</Typography>}
      <Box sx={{ mt: 3 }}>
        {spec && spec.elements.length === 0 && <Typography color="text.secondary">This dashboard is empty.</Typography>}
        {spec && spec.elements.length > 0 && <SpecRenderer spec={spec} data={data} collapseBelow={COLLAPSE_BELOW} />}
      </Box>
    </>
  );
}
