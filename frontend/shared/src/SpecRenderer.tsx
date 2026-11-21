import { Component, type ReactNode } from "react";
import { ChartView, Notice } from "./ChartView";
import { KpiView, TableView } from "./TableKpi";
import type { DataEntry, DataMap, ElementData, ViewElement, ViewSpec } from "./spec";

const ROW_PX = 56; // height of one layout row unit

function normalize(entry: DataMap[string]): DataEntry | undefined {
  if (!entry) return undefined;
  return "status" in entry ? entry : { status: "ok", data: entry as ElementData };
}

/** One failing element must never take the page down. */
class Boundary extends Component<{ children: ReactNode }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(e: Error) { return { err: e.message }; }
  render() { return this.state.err ? <Notice tone="error" text={`Could not render: ${this.state.err}`} /> : this.props.children; }
}

function Skeleton() {
  return (
    <div className="h-full animate-pulse space-y-3 p-4">
      <div className="h-3 w-1/3 rounded bg-slate-200" />
      <div className="h-full max-h-32 rounded bg-slate-100" />
    </div>
  );
}

function Body({ el, entry }: { el: ViewElement; entry: DataEntry | undefined }) {
  if (el.type === "text") return <div className="whitespace-pre-wrap p-3 text-sm">{el.title}</div>;
  if (!entry) return <Notice text="No data yet. Select the element and press Run." />;
  if (entry.status === "loading") return <Skeleton />;
  if (entry.status === "error") return <Notice tone="error" text={entry.message} />;
  if (el.type === "chart" && el.chart) return <ChartView chart={el.chart} data={entry.data} />;
  if (el.type === "table") return <TableView data={entry.data} format={el.format ?? {}} />;
  if (el.type === "kpi") return <KpiView data={entry.data} format={el.format ?? {}} />;
  return <Notice tone="error" text="Unsupported element" />;
}

export interface SpecRendererProps {
  spec: ViewSpec;
  data: DataMap;
  selectedId?: string | null;
  onSelect?: (elementId: string) => void; // optional hook: the Builder uses it, the Viewer doesn't
}

/** Renders a ViewSpec on a 12-column grid. Pure: no fetching, no builder-only logic. */
export function SpecRenderer({ spec, data, selectedId, onSelect }: SpecRendererProps) {
  const byId = new Map(spec.elements.map((e) => [e.id, e]));
  const items = spec.layout.filter((l) => byId.has(l.element_id));
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(12, minmax(0, 1fr))", gridAutoRows: ROW_PX }}>
      {items.map((l) => {
        const el = byId.get(l.element_id)!;
        const x = Math.min(Math.max(l.x, 0), 11);
        const w = Math.min(Math.max(l.w, 1), 12 - x);
        return (
          <section
            key={el.id}
            onClick={onSelect ? () => onSelect(el.id) : undefined}
            style={{ gridColumn: `${x + 1} / span ${w}`, gridRow: `${l.y + 1} / span ${Math.max(l.h, 1)}` }}
            className={`flex min-h-0 flex-col overflow-hidden rounded-lg border bg-white shadow-sm ${onSelect ? "cursor-pointer" : ""} ${selectedId === el.id ? "border-blue-500 ring-2 ring-blue-200" : "border-slate-200"}`}
          >
            {el.title && el.type !== "text" && <h3 className="truncate px-3 pt-2 text-sm font-medium text-slate-700">{el.title}</h3>}
            <div className="relative min-h-0 flex-1">
              <div className="absolute inset-0 overflow-hidden">
                <Boundary><Body el={el} entry={normalize(data[el.id])} /></Boundary>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}
