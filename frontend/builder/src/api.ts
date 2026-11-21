import { getIdToken } from "./auth";

const BASE = import.meta.env.VITE_API_URL ?? ""; // empty = same origin (Vite proxies /api in dev)

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

function detailText(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d))
    return d.map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`).join("; ");
  return fallback;
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const token = await getIdToken();
  if (!token) {
    onUnauthorized();
    throw new ApiError(401, "Signed out");
  }
  const res = await fetch(`${BASE}/api${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json: unknown = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* non-JSON error body */ }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) throw new ApiError(res.status, detailText(json, text.slice(0, 300) || res.statusText));
  return json as T;
}

export const get = <T,>(p: string) => api<T>("GET", p);
export const post = <T,>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const put = <T,>(p: string, b: unknown) => api<T>("PUT", p, b);

// ---- API types (mirror backend responses) ----
export interface Client { slug: string; name: string; created_at: string; created_by: string }
export interface Project { client_slug: string; slug: string; name: string; status: string; created_at: string; config?: ProjectConfig }
export interface ProjectConfig { bq_project: string | null; tables: string[]; max_bytes_billed: number | null; default_max_bytes_billed?: number }
export interface ViewSummary {
  client_slug: string; project_slug: string; slug: string; name: string;
  draft_revision: number | null; published_revision: number | null; has_unpublished_changes: boolean; updated_at: string;
}
export interface Revision { revision: number; updated_at: string | null; updated_by: string | null; is_draft: boolean; is_published: boolean }
export interface ColumnInfo { name: string; type: string; mode: string; bq_description: string; fields?: ColumnInfo[] }
export interface SchemaSnapshot { table: string; num_rows: number | null; num_bytes: number | null; columns: ColumnInfo[]; descriptions: Record<string, string> }
