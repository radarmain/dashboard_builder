import { API_BASE, ApiError, detailText, getIdToken, notifyUnauthorized, del as coreDel, patch as corePatch, post as corePost, put as corePut } from "@rdb/core";
import { invalidateForMutation } from "./navTree";

export { ApiError, get } from "@rdb/core";

// Mutations refresh the sidebar tree (navTree.ts decides which cache entries are stale).
export const post = async <T,>(p: string, b?: unknown) => { const r = await corePost<T>(p, b); invalidateForMutation("POST", p); return r; };
export const put = async <T,>(p: string, b: unknown) => { const r = await corePut<T>(p, b); invalidateForMutation("PUT", p); return r; };
export const patch = async <T,>(p: string, b: unknown) => { const r = await corePatch<T>(p, b); invalidateForMutation("PATCH", p); return r; };
export const del = async <T,>(p: string) => { const r = await coreDel<T>(p); invalidateForMutation("DELETE", p); return r; };

// ---- API types (mirror backend responses) ----
export type Lifecycle = "active" | "archived";
/** `lifecycle` is the item's own state; `effective_lifecycle` also counts archived ancestors. */
interface LifecycleFields { lifecycle: Lifecycle; effective_lifecycle: Lifecycle; archived_at?: string | null; archived_by?: string | null }
export interface Client extends LifecycleFields { slug: string; name: string; created_at: string; created_by: string }
export interface Project extends LifecycleFields { client_slug: string; slug: string; name: string; status: string; created_at: string; config?: ProjectConfig }
export interface FavRef { type: "client" | "project" | "view"; client: string; project?: string; view?: string }
export interface Favorite extends FavRef { name: string; path: string; status: string | null; created_at: string }
export interface DeletePreview {
  slug: string; name: string; lifecycle: Lifecycle; projects: number; views: number; revisions: number;
  s3_objects: number; s3_bytes: number; client_users: number;
}
export interface TableauLink { workbook_id: string; workbook_name: string }
export interface ProjectConfig { bq_project: string | null; tables: string[]; max_bytes_billed: number | null; default_max_bytes_billed?: number; tableau_link?: TableauLink | null }

// ---- Tableau import (sanitized plan: no credentials, no data) ----
export type SheetStatus = "supported" | "approximate" | "unsupported";
export interface PlanField { field: string; [k: string]: unknown }
export interface PlanSheet {
  name: string; chart_type: string; status: SheetStatus; status_reason: string; dual_axis: boolean;
  rows: PlanField[]; columns: PlanField[]; filters: { field: string; type?: string; default?: string; interactive_in_tableau?: boolean }[];
  dashboards: string[]; grid?: { x: number; y: number; w: number; h: number };
  calculations_used: { name: string; formula: string; kind: string }[];
}
export interface ImportPlan {
  datasources: { name: string; connection_classes?: string[]; mode?: string; tables: string[]; filters?: unknown[]; joins?: unknown[] }[];
  calculated_fields: { name: string; formula: string; kind: string }[];
  parameters: { name: string; [k: string]: unknown }[];
  worksheets: PlanSheet[];
  dashboards: { name: string; sheets: { name: string }[] }[];
}
export interface PlanResponse { import_id: string; workbook: TableauLink; plan: ImportPlan }
export interface ReconStats {
  matched_rows?: number; mismatched_rows?: number; rows_only_in_reference?: number; rows_only_in_result?: number;
  max_relative_difference?: number; duplicate_rows_collapsed?: number; ok?: boolean; error?: string; examples?: unknown[];
  [k: string]: unknown;
}
export type EntryStatus = "unverified" | "needs_review" | "verified" | "differs" | "skipped";
export interface ImportEntry {
  sheet: string; mode?: "structure"; plan_status: SheetStatus; status: EntryStatus; status_reason: string; element_id: string | null;
  sql: string | null; filters_baked: string[]; assumptions: string[]; warnings: string[]; notes?: string[];
  result_columns?: { name: string; type: string }[]; dry_run_bytes?: number | null;
  source_is_view?: boolean; base_tables?: string[]; config?: Record<string, unknown> | null;
  reconciliation: ReconStats & { result?: string };
  verify?: { result: string; detail?: string };
  bytes_scanned: number;
}
/** 409 body from POST /query (gate) and the verify stream: the run needs an explicit OK. */
export interface GateInfo {
  needs_confirmation: true; reasons: string[]; estimated_bytes: number; source_is_view: boolean;
  base_tables: string[]; suggestion: string;
}
export interface PublishGuard { needs_confirmation: true; reason: "unreviewed_imports"; elements: { id: string; title: string; status: string; sheet: string }[] }
export interface ViewSummary extends LifecycleFields {
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
  | { type: "report"; entry: ImportEntry }
  | ({ type: "needs_confirmation"; sheet: string } & GateInfo)
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
