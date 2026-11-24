import { Icon, type IconName } from "@rdb/core";

const KINDS = new Set(["bar", "line", "area", "pie", "scatter", "table", "kpi", "dashboard"]);

/** Icon for a chart kind / element type (Font Awesome via the core registry). */
export function KindIcon({ kind, size = 28 }: { kind: string; size?: number }) {
  return <Icon name={`kind-${KINDS.has(kind) ? kind : "dashboard"}` as IconName} size={size * 0.75} />;
}
