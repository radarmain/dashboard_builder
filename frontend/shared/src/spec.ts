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
}

export interface ViewElement {
  id: string;
  type: ElementType;
  title: string;
  chart: ChartDef | null;
  sql: string;
  format: Record<string, string>; // table/kpi column formats
  cache_ttl_seconds: number;
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
  | { status: "error"; message: string }
  | { status: "ok"; data: ElementData };

export type DataMap = Record<string, DataEntry | ElementData | undefined>;
