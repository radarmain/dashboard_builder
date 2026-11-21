/** Tiny inline icons for chart kinds / element types (currentColor, no icon package). */
const common = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" } as const;

const PATHS: Record<string, JSX.Element> = {
  bar: <g {...common}><path d="M5 20V12M12 20V4M19 20v-6" /></g>,
  line: <g {...common}><path d="M3 17l6-6 4 4 8-8" /></g>,
  area: <g {...common}><path d="M3 19v-6l5-5 4 4 5-6 4 4v9z" fill="currentColor" fillOpacity={0.25} /></g>,
  pie: <g {...common}><path d="M12 3a9 9 0 1 0 9 9h-9z" /><path d="M15 2.6A9 9 0 0 1 21.4 9H15z" /></g>,
  scatter: <g fill="currentColor"><circle cx="6" cy="16" r="2" /><circle cx="11" cy="9" r="2" /><circle cx="16" cy="14" r="2" /><circle cx="19" cy="6" r="2" /></g>,
  table: <g {...common}><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M4 10h16M10 10v9" /></g>,
  kpi: <g {...common}><path d="M4 17l5-5 3 3 8-8M15 7h5v5" /></g>,
  dashboard: <g {...common}><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="4" rx="1.5" /><rect x="13" y="11" width="7" height="9" rx="1.5" /><rect x="4" y="14" width="7" height="6" rx="1.5" /></g>,
};

export function KindIcon({ kind, size = 28 }: { kind: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
      {PATHS[kind] ?? PATHS.dashboard}
    </svg>
  );
}
