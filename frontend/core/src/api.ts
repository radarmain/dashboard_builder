import { getIdToken } from "./auth";

export const API_BASE: string = import.meta.env.VITE_API_URL ?? ""; // empty = same origin (Vite proxies /api in dev)

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown, // parsed `detail` when the server sent an object (e.g. 409 confirmation)
  ) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}
export const notifyUnauthorized = () => onUnauthorized();

export function detailText(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && !Array.isArray(d)) return "Confirmation required";
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
  const res = await fetch(`${API_BASE}/api${path}`, {
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
  if (!res.ok) {
    const d = (json as { detail?: unknown } | null)?.detail;
    throw new ApiError(res.status, detailText(json, text.slice(0, 300) || res.statusText), typeof d === "object" ? d : undefined);
  }
  return json as T;
}

export const get = <T,>(p: string) => api<T>("GET", p);
export const post = <T,>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const put = <T,>(p: string, b: unknown) => api<T>("PUT", p, b);
export const patch = <T,>(p: string, b: unknown) => api<T>("PATCH", p, b);
export const del = <T,>(p: string) => api<T>("DELETE", p);
