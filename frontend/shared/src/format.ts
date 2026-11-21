// Value coercion + formatting. The backend returns decimals as strings and dates as ISO strings.

const NUM_RE = /^-?\d+(\.\d+)?([eE][-+]?\d+)?$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[-+]\d{2}:?\d{2})?)?$/;

export function toNumber(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && NUM_RE.test(v.trim())) return Number(v);
  return null;
}

export function isDateString(v: unknown): v is string {
  return typeof v === "string" && DATE_RE.test(v) && !Number.isNaN(Date.parse(v));
}

/** Date -> epoch ms (null if it isn't a date). Date-only strings are treated as UTC. */
export function toTime(v: unknown): number | null {
  if (!isDateString(v)) return null;
  const t = Date.parse(v.length === 10 ? `${v}T00:00:00Z` : v);
  return Number.isNaN(t) ? null : t;
}

/** True if (almost) every non-null sample is a number-like value. */
export function columnIsNumeric(rows: unknown[][], idx: number): boolean {
  let seen = 0;
  for (const r of rows.slice(0, 200)) {
    const v = r[idx];
    if (v === null || v === undefined || v === "") continue;
    seen++;
    if (toNumber(v) === null) return false;
  }
  return seen > 0;
}

export function columnIsDate(rows: unknown[][], idx: number): boolean {
  let seen = 0;
  for (const r of rows.slice(0, 200)) {
    const v = r[idx];
    if (v === null || v === undefined || v === "") continue;
    seen++;
    if (toTime(v) === null) return false;
  }
  return seen > 0;
}

export function formatValue(v: unknown, hint?: string): string {
  if (v === null || v === undefined || v === "") return "";
  const n = toNumber(v);
  if (n !== null && (hint === "currency" || hint === "percent" || hint === "number" || !hint)) {
    if (hint === "currency")
      return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: Math.abs(n) >= 1000 ? 0 : 2 });
    if (hint === "percent") return `${(n * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    return n.toLocaleString(undefined, { maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 });
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function formatDate(ms: number, withTime = false): string {
  const d = new Date(ms);
  return withTime ? d.toLocaleString() : d.toLocaleDateString(undefined, { timeZone: "UTC" });
}
