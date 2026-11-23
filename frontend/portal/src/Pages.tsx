import { useEffect, useState, type ReactNode } from "react";
import { Box, Link, Typography } from "@mui/material";
import { CardSkeletons, ViewCard, cardGridSx } from "./ViewCard";
import { PortalBreadcrumbs, useDocTitle, useHideClientName, useReportClient } from "./PortalShell";
import { portalApi, rememberClient, useFetched, type ProjectCards } from "./api";
import { publicClientUrl } from "./routes";
import { usePortal, usePortalParams } from "./context";

/** Host mode only: a client user on someone else's subdomain gets a link to their own (if exactly one). */
function useOwnClientUrl(): string | null {
  const { hostClient, preview } = usePortal();
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!hostClient || preview) return;
    portalApi.me().then(
      (cs) => setUrl(cs.length === 1 && cs[0].slug !== hostClient ? publicClientUrl(cs[0].slug) : null),
      () => {},
    );
  }, [hostClient, preview]);
  return url;
}

export function NotFound({ title = "We couldn't find that page", body = "It may have moved, or it isn't available to you." }: { title?: string; body?: string }) {
  const own = useOwnClientUrl();
  useHideClientName(); // access isn't established: header shows the RADaR logo only
  return (
    <Box sx={{ py: 8, textAlign: "center" }}>
      <Typography variant="h5" sx={{ mb: 1 }}>{title}</Typography>
      <Typography color="text.secondary">{body}</Typography>
      {own && (
        <Typography sx={{ mt: 2 }}>
          <Link href={own}>Go to your dashboards</Link>
        </Typography>
      )}
    </Box>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <Box sx={(t) => ({ py: 8, px: 3, textAlign: "center", borderRadius: "20px", bgcolor: t.palette.tokens.surfaceContainerLow })}>
      <Typography color="text.secondary">{children}</Typography>
    </Box>
  );
}

const EMPTY = "No dashboards are available yet. Contact your RADaR team.";

function Section({ client, project, heading }: { client: string; project: ProjectCards; heading: boolean }) {
  return (
    <Box component="section" sx={{ mb: 4 }}>
      {heading && <Typography variant="h6" component="h2" sx={{ mb: 2 }}>{project.name}</Typography>}
      <Box sx={cardGridSx}>{project.views.map((v) => <ViewCard key={`${v.project_slug}/${v.slug}`} client={client} view={v} />)}</Box>
    </Box>
  );
}

/** Cards for every published view of the client, grouped by project. */
export function ClientHome() {
  const { client } = usePortalParams();
  const f = useFetched(`home:${client}`, () => portalApi.client(client));
  if (f.data) rememberClient(f.data.client);
  useReportClient(client, f.data?.client.name);
  useDocTitle(f.data?.client.name);

  if (f.notFound) return <NotFound />;
  if (f.error) return <NotFound title="Something went wrong" body={f.error} />;
  return (
    <>
      <PortalBreadcrumbs items={[{ label: f.data?.client.name ?? "…" }]} />
      <Typography variant="h4" component="h1" sx={{ mb: 3 }}>Dashboards</Typography>
      {f.loading && <CardSkeletons />}
      {f.data && f.data.projects.length === 0 && <EmptyState>{EMPTY}</EmptyState>}
      {f.data?.projects.map((p) => <Section key={p.slug} client={client} project={p} heading />)}
    </>
  );
}

/** The same cards for one project. */
export function ProjectPage() {
  const { client, project } = usePortalParams();
  const { links } = usePortal();
  const f = useFetched(`project:${client}/${project}`, () => portalApi.project(client, project));
  if (f.data) rememberClient(f.data.client);
  useReportClient(client, f.data?.client.name);
  useDocTitle(f.data?.project.name, f.data?.client.name);

  if (f.notFound) return <NotFound />;
  if (f.error) return <NotFound title="Something went wrong" body={f.error} />;
  return (
    <>
      <PortalBreadcrumbs items={[{ label: f.data?.client.name ?? "…", to: links.client(client) }, { label: f.data?.project.name ?? "…" }]} />
      <Typography variant="h4" component="h1" sx={{ mb: 3 }}>{f.data?.project.name ?? ""}</Typography>
      {f.loading && <CardSkeletons />}
      {f.data && f.data.project.views.length === 0 && <EmptyState>{EMPTY}</EmptyState>}
      {f.data && f.data.project.views.length > 0 && <Section client={client} project={f.data.project} heading={false} />}
    </>
  );
}
