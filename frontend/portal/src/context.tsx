import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { portalLinks, type PortalLinks } from "./routes";

interface PortalCtx {
  basePath: string;
  preview: boolean;
  /** Host mode: the client slug taken from the subdomain; null in path mode. */
  hostClient: string | null;
  links: PortalLinks;
}

const Ctx = createContext<PortalCtx | null>(null);

/** Wrap portal routes. preview=true hides the account menu (Builder's Client Preview). */
export function PortalProvider({ basePath, preview = false, hostClient = null, children }: { basePath: string; preview?: boolean; hostClient?: string | null; children: ReactNode }) {
  const value = useMemo(() => ({ basePath, preview, hostClient, links: portalLinks(basePath, hostClient) }), [basePath, preview, hostClient]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePortal(): PortalCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePortal outside <PortalProvider>");
  return v;
}

/** client/project/view from the URL (path mode) or the subdomain (host mode). */
export function usePortalParams() {
  const p = useParams();
  const { hostClient } = usePortal();
  return { client: hostClient ?? p.client ?? "", project: p.project ?? "", view: p.view ?? "" };
}
