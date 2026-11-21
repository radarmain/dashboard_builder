/**
 * The ONLY place portal URLs are built or declared. Two modes (docs/ARCHITECTURE.md):
 *  - path mode (localhost, *.cloudfront.net, Builder preview):
 *      `${base}/c/:client`, `${base}/c/:client/p/:project`, `${base}/c/:client/p/:project/v/:view`
 *  - host mode (https://<client>.radar-analytics.com): the client slug is the subdomain;
 *      `/` (client home), `/:project`, `/:project/:view`
 * Links keep the same (client, project, view) signature in both modes; host mode ignores `client`.
 */
import reserved from "../../../backend/app/reserved_slugs.json";

export interface PortalLinks {
  home: string;
  client: (c: string) => string;
  project: (c: string, p: string) => string;
  view: (c: string, p: string, v: string) => string;
}

export const BASE_DOMAIN: string = import.meta.env.VITE_BASE_DOMAIN || "radar-analytics.com";
/** Same file the backend validates against (backend/app/reserved_slugs.json). */
export const RESERVED_SLUGS: ReadonlySet<string> = new Set(reserved);

/** Client slug if `hostname` is `<label>.<BASE_DOMAIN>` for a normal (non-reserved) label, else null. */
export function hostClientFor(hostname: string, baseDomain: string = BASE_DOMAIN): string | null {
  const h = hostname.toLowerCase();
  const suffix = `.${baseDomain}`;
  if (!h.endsWith(suffix)) return null;
  const label = h.slice(0, -suffix.length);
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(label) || RESERVED_SLUGS.has(label)) return null;
  return label;
}

const trim = (base: string) => base.replace(/\/+$/, "");

/**
 * Links for <Link to=...> / navigate(). basePath is "" (viewer) or "/preview" (Builder).
 * hostClient set = host mode (only ever with basePath "").
 */
export function portalLinks(basePath: string, hostClient?: string | null): PortalLinks {
  if (hostClient) {
    return { home: "/", client: () => "/", project: (_c, p) => `/${p}`, view: (_c, p, v) => `/${p}/${v}` };
  }
  const b = trim(basePath);
  return {
    home: b || "/",
    client: (c) => `${b}/c/${c}`,
    project: (c, p) => `${b}/c/${c}/p/${p}`,
    view: (c, p, v) => `${b}/c/${c}/p/${p}/v/${v}`,
  };
}

/** Route patterns for <Route path=...>. */
export function portalPatterns(basePath: string, hostClient?: string | null) {
  if (hostClient) return { client: "/", project: "/:project", view: "/:project/:view", notFound: "*" };
  const b = trim(basePath);
  return {
    client: `${b}/c/:client`,
    project: `${b}/c/:client/p/:project`,
    view: `${b}/c/:client/p/:project/v/:view`,
    notFound: `${b}/*`,
  };
}

/** Public (client-facing) URLs on the custom domain, e.g. for "Copy client link". */
export const publicClientUrl = (slug: string, baseDomain: string = BASE_DOMAIN) => `https://${slug}.${baseDomain}`;
export const publicViewUrl = (slug: string, project: string, view: string, baseDomain: string = BASE_DOMAIN) =>
  `${publicClientUrl(slug, baseDomain)}/${project}/${view}`;
