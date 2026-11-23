import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import { ChartView, Notice } from "./ChartView";
import { KpiView, TableView } from "./TableKpi";
import { T } from "./tokens";
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
    <div style={{ height: "100%", padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="rdb-skeleton" style={{ height: 12, width: "33%", borderRadius: 6 }} />
      <div className="rdb-skeleton" style={{ flex: 1, maxHeight: 128, borderRadius: 12 }} />
    </div>
  );
}

/** Placeholder for an element whose query hasn't been run yet (Builder-only lazy import). */
function Idle({ text, onRun }: { text: string; onRun?: () => void }) {
  return (
    <div style={{ display: "flex", height: "100%", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, padding: 12, textAlign: "center", fontSize: 14, color: T.onSurfaceVariant }}>
      <span>{text}</span>
      {onRun && (
        <button type="button" onClick={(e) => { e.stopPropagation(); onRun(); }}
          style={{ font: "inherit", cursor: "pointer", padding: "6px 16px", borderRadius: 999, border: `1px solid ${T.primary}`, background: "transparent", color: T.primary }}>
          Run
        </button>
      )}
    </div>
  );
}

function Body({ el, entry }: { el: ViewElement; entry: DataEntry | undefined }) {
  if (el.type === "text") return <div style={{ whiteSpace: "pre-wrap", padding: 12, fontSize: 14, color: T.onSurface }}>{el.title}</div>;
  if (!entry) return <Notice text="No data yet. Select the element and press Run." />;
  if (entry.status === "loading") return <Skeleton />;
  if (entry.status === "idle") return <Idle text={entry.text} onRun={entry.onRun} />;
  if (entry.status === "error") return <Notice tone="error" text={entry.message} />;
  if (el.type === "chart" && el.chart) return <ChartView chart={el.chart} data={entry.data} />;
  if (el.type === "table") return <TableView data={entry.data} format={el.format ?? {}} pivot={el.pivot} heatmap={el.heatmap} />;
  if (el.type === "kpi") return <KpiView data={entry.data} format={el.format ?? {}} />;
  return <Notice tone="error" text="Unsupported element" />;
}

export interface SpecRendererProps {
  spec: ViewSpec;
  data: DataMap;
  selectedId?: string | null;
  onSelect?: (elementId: string) => void; // optional hook: the Builder uses it, the Viewer doesn't
  /** element id -> version; a version > 0 mounts the element with a brief fade/scale-in (and remounts when it changes). */
  popVersions?: Record<string, number>;
  /** Container width (px) below which the grid collapses to one column, in layout order (y, then x). */
  collapseBelow?: number;
}

/** Renders a ViewSpec on a 12-column grid. Pure: no fetching, no builder-only logic. */
export function SpecRenderer({ spec, data, selectedId, onSelect, popVersions, collapseBelow }: SpecRendererProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!collapseBelow || !node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < collapseBelow));
    ro.observe(node);
    return () => ro.disconnect();
  }, [collapseBelow]);

  const byId = new Map(spec.elements.map((e) => [e.id, e]));
  let items = spec.layout.filter((l) => byId.has(l.element_id));
  if (narrow) items = [...items].sort((a, b) => a.y - b.y || a.x - b.x);
  return (
    <div
      ref={ref}
      style={{
        display: "grid", gap: 12, fontFamily: T.font,
        gridTemplateColumns: narrow ? "minmax(0, 1fr)" : "repeat(12, minmax(0, 1fr))",
        gridAutoRows: narrow ? undefined : ROW_PX,
      }}
    >
      {items.map((l) => {
        const el = byId.get(l.element_id)!;
        const x = Math.min(Math.max(l.x, 0), 11);
        const w = Math.min(Math.max(l.w, 1), 12 - x);
        const h = Math.max(l.h, 1);
        const pop = popVersions?.[el.id] ?? 0;
        return (
          <section
            key={`${el.id}:${pop}`}
            onClick={onSelect ? () => onSelect(el.id) : undefined}
            onKeyDown={onSelect ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(el.id); } } : undefined}
            tabIndex={onSelect ? 0 : undefined}
            className={`rdb-card ${onSelect ? "rdb-card--selectable" : ""} ${pop ? "rdb-pop" : ""}`}
            style={{
              ...(narrow
                ? { height: h * ROW_PX + (h - 1) * 12 }
                : { gridColumn: `${x + 1} / span ${w}`, gridRow: `${l.y + 1} / span ${h}` }),
              display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden",
              background: T.surface, borderRadius: 16, color: T.onSurface,
              border: `${selectedId === el.id ? 2 : 1}px solid ${selectedId === el.id ? T.primary : T.outlineVariant}`,
              margin: selectedId === el.id ? 0 : 1,
            }}
          >
            {el.title && el.type !== "text" && (
              <h3 style={{ margin: 0, padding: "10px 16px 0", fontSize: 14, lineHeight: "20px", fontWeight: 500, letterSpacing: 0.1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{el.title}</h3>
            )}
            <div style={{ position: "relative", minHeight: 0, flex: 1 }}>
              <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
                <Boundary><Body el={el} entry={normalize(data[el.id])} /></Boundary>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}
