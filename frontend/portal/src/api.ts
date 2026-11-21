import { useEffect, useState } from "react";
import { ApiError, get } from "@rdb/core";

export interface ViewCardData {
  slug: string;
  name: string;
  description: string | null;
  project_slug: string;
  project_name: string;
  published_at: string | null;
  counts: { chart?: number; table?: number; kpi?: number; text?: number };
  chart_kinds: string[];
}
export interface ProjectCards { slug: string; name: string; views: ViewCardData[] }
export interface ClientRef { slug: string; name: string }
export interface ClientHomeData { client: ClientRef; projects: ProjectCards[] }
export interface ProjectData { client: ClientRef; project: ProjectCards }

export interface PortalElement {
  id: string;
  type: "chart" | "table" | "kpi" | "text";
  title: string;
  chart: { kind: "bar" | "line" | "area" | "pie" | "scatter"; x: string; y: string[]; series: string | null; stacked: boolean; format: Record<string, string> } | null;
  format: Record<string, string>;
}
export interface PortalViewData {
  client: ClientRef;
  project: { slug: string; name: string };
  name: string;
  description: string | null;
  published_at: string | null;
  layout: { element_id: string; x: number; y: number; w: number; h: number }[];
  elements: PortalElement[];
}
export interface PortalData { columns: string[]; rows: unknown[][]; truncated: boolean }

export const portalApi = {
  me: () => get<ClientRef[]>("/portal/me"),
  client: (c: string) => get<ClientHomeData>(`/portal/clients/${c}`),
  project: (c: string, p: string) => get<ProjectData>(`/portal/clients/${c}/projects/${p}`),
  view: (c: string, p: string, v: string) => get<PortalViewData>(`/portal/clients/${c}/projects/${p}/views/${v}`),
  data: (c: string, p: string, v: string, el: string) => get<PortalData>(`/portal/clients/${c}/projects/${p}/views/${v}/data/${el}`),
};

export interface Fetched<T> { data: T | null; loading: boolean; notFound: boolean; error: string | null }

/** Tiny fetch hook: re-runs when `key` changes. */
export function useFetched<T>(key: string, load: () => Promise<T>): Fetched<T> {
  const [state, setState] = useState<Fetched<T>>({ data: null, loading: true, notFound: false, error: null });
  useEffect(() => {
    let live = true;
    setState({ data: null, loading: true, notFound: false, error: null });
    load().then(
      (data) => live && setState({ data, loading: false, notFound: false, error: null }),
      (e) => {
        if (!live) return;
        const nf = e instanceof ApiError && e.status === 404;
        setState({ data: null, loading: false, notFound: nf, error: nf ? null : "Something went wrong loading this page. Please try again." });
      },
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}

// Client name by slug, so the top bar doesn't flash empty between pages.
const names = new Map<string, string>();
export const rememberClient = (c: ClientRef) => names.set(c.slug, c.name);
export const clientNameOf = (slug: string) => names.get(slug) ?? "";
