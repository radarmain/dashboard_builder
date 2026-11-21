import { API_BASE, ApiError, detailText, getIdToken, notifyUnauthorized } from "@rdb/core";

export { ApiError, del, get, post, put, patch } from "@rdb/core";

// ---- API types (mirror backend responses) ----
export interface Client { slug: string; name: string; created_at: string; created_by: string }
export interface Project { client_slug: string; slug: string; name: string; status: string; created_at: string; config?: ProjectConfig }
export interface ProjectConfig { bq_project: string | null; tables: string[]; max_bytes_billed: number | null; default_max_bytes_billed?: number }
export interface ViewSummary {
  client_slug: string; project_slug: string; slug: string; name: string; description?: string;
  draft_revision: number | null; published_revision: number | null; has_unpublished_changes: boolean; updated_at: string;
}
export interface Revision { revision: number; updated_at: string | null; updated_by: string | null; is_draft: boolean; is_published: boolean }
export interface ColumnInfo { name: string; type: string; mode: string; bq_description: string; fields?: ColumnInfo[] }
export interface SchemaSnapshot { table: string; num_rows: number | null; num_bytes: number | null; columns: ColumnInfo[]; descriptions: Record<string, string> }

// ---- chat streaming (fetch + ReadableStream; EventSource can't send the bearer token) ----
export type ChatEvent =
  | { type: "status"; text: string }
  | { type: "text"; delta: string }
  | { type: "changes"; operations: ChatOp[] }
  | { type: "error"; message: string }
  | { type: "done" };

export type ChatOp =
  | { op: "add_element"; element: import("@rdb/shared").ViewElement; layout: { x: number; y: number; w: number; h: number } }
  | { op: "update_element"; element: import("@rdb/shared").ViewElement; layout?: { x: number; y: number; w: number; h: number } }
  | { op: "remove_element"; id: string };

export async function streamChat(path: string, body: unknown, signal: AbortSignal, onEvent: (e: ChatEvent) => void): Promise<void> {
  const token = await getIdToken();
  if (!token) {
    notifyUnauthorized();
    throw new ApiError(401, "Signed out");
  }
  const res = await fetch(`${API_BASE}/api${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) {
    notifyUnauthorized();
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok || !res.body) {
    const text = await res.text();
    let json: unknown = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    throw new ApiError(res.status, detailText(json, text.slice(0, 300) || res.statusText));
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as ChatEvent);
    }
  }
}
