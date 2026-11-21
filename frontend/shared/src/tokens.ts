/** Theme tokens for shared components: --rdb-* CSS variables (set from the app's theme.ts) with
 *  fallbacks so this package renders sensibly with no theme at all. No MUI in this package. */
const v = (name: string, fallback: string) => `var(--rdb-${name}, ${fallback})`;

export const T = {
  font: v("font", 'Roboto, system-ui, -apple-system, "Segoe UI", sans-serif'),
  primary: v("primary", "#421540"),
  primaryContainer: v("primary-container", "#F2D9EF"),
  onPrimaryContainer: v("on-primary-container", "#2B0A2A"),
  secondary: v("secondary", "#00B8B0"),
  secondaryContainer: v("secondary-container", "#C4F1ED"),
  error: v("error", "#B3261E"),
  surface: v("surface-container-lowest", "#FFFFFF"),
  surfaceLow: v("surface-container-low", "#F6F2F6"),
  surfaceVariant: v("surface-container", "#F1EDF1"),
  surfaceHigh: v("surface-container-high", "#EBE7EB"),
  onSurface: v("on-surface", "#3E3E3E"),
  onSurfaceVariant: v("on-surface-variant", "#737373"),
  outline: v("outline", "#CFC4CD"),
  outlineVariant: v("outline-variant", "#E4DCE3"),
};

/** Categorical, colorblind-friendly series palette (in order). Deliberately NOT brand colors. */
export const CHART_COLORS = ["#1A73E8", "#E8710A", "#188038", "#A142F4", "#D93025", "#12B5CB", "#F9AB00", "#80868B"] as const;
