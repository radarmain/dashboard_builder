// Mirrors docs/VIEW_SPEC.md and backend/app/models.py (Pydantic). Keep in sync.
export type ChartKind = "bar" | "line" | "area" | "pie" | "scatter";
export type ElementType = "chart" | "table" | "kpi" | "text";
export type FormatHint = "currency" | "percent" | "number";

export interface ChartDef {
  kind: ChartKind;
  x: string;
  y: string[];
  series: string | null;
  stacked: boolean;
  format: Record<string, string>; // column -> currency | percent | number
  outline?: boolean; // area: heavier stroke on top of the fill
  labels?: boolean; // value labels on points/bars
}

/** Table pivot: one row per `row` value, one column per `column` value, cells = `value`. */
export interface PivotDef {
  row: string;
  column: string;
  value: string;
}

/** Cell coloring for pivot tables. diverging = red (min) to green (max); sequential = light to dark. */
export interface HeatmapDef {
  palette: "diverging" | "sequential";
  min: number;
  max: number;
}

/** Provenance of an element created by the Tableau import (a scaffold: analysts verify it). */
export type ImportStatus = "unverified" | "needs_review" | "verified" | "differs";
export interface ImportMeta {
  source: "tableau";
  sheet_name: string;
  status: ImportStatus;
  notes: string[];
  dry_run_bytes: number | null;
  source_is_view: boolean;
  base_tables: string[];
}

export interface ViewElement {
  id: string;
  type: ElementType;
  title: string;
  chart: ChartDef | null;
  pivot?: PivotDef | null; // table only
  heatmap?: HeatmapDef | null; // table only (needs pivot)
  sql: string;
  format: Record<string, string>; // table/kpi column formats
  cache_ttl_seconds: number;
  import?: ImportMeta; // Tableau import only (never sent to clients)
  review_status?: "reviewed"; // analyst sign-off for imported elements
}

/** 12-column grid: x + w <= 12. */
export interface LayoutItem {
  element_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ViewSpec {
  schema_version: 1;
  view: { name: string; slug: string };
  revision: number; // server-assigned
  updated_at: string; // server-assigned
  updated_by: string; // server-assigned
  layout: LayoutItem[];
  elements: ViewElement[];
}

/** Result shape of POST /query and GET .../data/{element_id} (extra fields ignored). */
export interface ElementData {
  columns: string[];
  rows: unknown[][];
  truncated?: boolean;
  bytes_processed?: number;
  cache_hit?: boolean;
}

/** Per-element data state fed to SpecRenderer. Missing key = not requested yet (shows skeleton). */
export type DataEntry =
  | { status: "loading" }
  | { status: "idle"; text: string; onRun?: () => void } // Builder only: lazy imported element
  | { status: "error"; message: string }
  | { status: "ok"; data: ElementData };

export type DataMap = Record<string, DataEntry | ElementData | undefined>;
