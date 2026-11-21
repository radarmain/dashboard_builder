import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Route } from "react-router-dom";
import { ClientHome, NotFound, ProjectPage } from "./Pages";
import { PortalShell } from "./PortalShell";
import { CardSkeletons } from "./ViewCard";
import { portalPatterns } from "./routes";

// Lazy: pulls in the shared renderer + recharts, which the home/project pages do not need.
const ViewPage = lazy(() => import("./ViewPage").then((m) => ({ default: m.ViewPage })));

/**
 * Portal <Route> elements (call as a function, not as a component: <Routes> only accepts <Route>
 * children). Wrap them in <PortalProvider basePath=...>.
 */
export function portalRoutes(basePath: string, extra?: ReactNode, hostClient?: string | null) {
  const p = portalPatterns(basePath, hostClient);
  return (
    <Route element={<PortalShell />}>
      <Route path={p.client} element={<ClientHome />} />
      <Route path={p.project} element={<ProjectPage />} />
      {extra}
      <Route path={p.view} element={<Suspense fallback={<CardSkeletons n={1} />}><ViewPage /></Suspense>} />
      <Route path={p.notFound} element={<NotFound />} />
    </Route>
  );
}
