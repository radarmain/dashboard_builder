/**
 * M3-style theme shared by the Builder and the Viewer (@rdb/core). The ONLY place (with the chart palette in @rdb/shared) where
 * hard-coded colors live. Tokens are structured by color role so a dark token set can be added
 * later: write a `darkTokens: Tokens` and pass it to buildTheme()/applyCssVars().
 *
 * Contrast rule: teal (secondary) is NEVER a text background with white text (about 2.5:1).
 * Use `onSecondary` (#00201E) on teal, or use teal only for accents/indicators/progress/focus rings.
 */
import { alpha, createTheme, type Shadows, type Theme } from "@mui/material/styles";

export interface Tokens {
  primary: string; onPrimary: string; primaryContainer: string; onPrimaryContainer: string;
  secondary: string; onSecondary: string; secondaryContainer: string; onSecondaryContainer: string;
  tertiary: string; onTertiary: string; tertiaryContainer: string; onTertiaryContainer: string;
  berry: string; rosewood: string;
  error: string; onError: string; errorContainer: string; onErrorContainer: string;
  warning: string; onWarning: string; warningContainer: string; onWarningContainer: string;
  success: string; onSuccess: string; successContainer: string; onSuccessContainer: string;
  background: string; onBackground: string;
  surface: string; surfaceContainerLowest: string; surfaceContainerLow: string; surfaceContainer: string;
  surfaceContainerHigh: string; surfaceContainerHighest: string;
  onSurface: string; onSurfaceVariant: string;
  outline: string; outlineVariant: string;
  scrim: string;
}

export const lightTokens: Tokens = {
  primary: "#421540", onPrimary: "#FFFFFF", primaryContainer: "#F2D9EF", onPrimaryContainer: "#2B0A2A",
  secondary: "#00B8B0", onSecondary: "#00201E", secondaryContainer: "#C4F1ED", onSecondaryContainer: "#00201E",
  tertiary: "#F68D76", onTertiary: "#3B0F05", tertiaryContainer: "#FFDBD2", onTertiaryContainer: "#3B0F05",
  berry: "#741545", rosewood: "#8A3449",
  error: "#B3261E", onError: "#FFFFFF", errorContainer: "#F9DEDC", onErrorContainer: "#410E0B",
  warning: "#8A5100", onWarning: "#FFFFFF", warningContainer: "#FFE2B8", onWarningContainer: "#2C1600",
  success: "#1B6B3A", onSuccess: "#FFFFFF", successContainer: "#C9EFD3", onSuccessContainer: "#00210F",
  background: "#FBF8FB", onBackground: "#3E3E3E",
  surface: "#FBF8FB", surfaceContainerLowest: "#FFFFFF", surfaceContainerLow: "#F6F2F6", surfaceContainer: "#F1EDF1",
  surfaceContainerHigh: "#EBE7EB", surfaceContainerHighest: "#E5E1E5",
  onSurface: "#3E3E3E", onSurfaceVariant: "#737373",
  outline: "#CFC4CD", outlineVariant: "#E4DCE3",
  scrim: "#000000",
};

const kebab = (s: string) => s.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());

/** Expose tokens as --rdb-* CSS variables so @rdb/shared can style itself without MUI. */
export function applyCssVars(t: Tokens, root: HTMLElement = document.documentElement): void {
  for (const [k, v] of Object.entries(t)) root.style.setProperty(`--rdb-${kebab(k)}`, v);
  root.style.setProperty("--rdb-font", FONT);
}

export const FONT = '"Roboto", system-ui, -apple-system, "Segoe UI", sans-serif';
export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

// M3 shape scale
export const shape = { xs: 4, sm: 8, md: 12, lg: 16, xl: 28, full: 999 };

declare module "@mui/material/Button" {
  interface ButtonPropsVariantOverrides { tonal: true }
}
declare module "@mui/material/styles" {
  interface Palette { tokens: Tokens }
  interface PaletteOptions { tokens?: Tokens }
}

const type = (size: number, line: number, weight: number, tracking: number) => ({
  fontFamily: FONT, fontSize: `${size / 16}rem`, lineHeight: `${line}px`, fontWeight: weight, letterSpacing: `${tracking}px`,
});

export function buildTheme(t: Tokens = lightTokens): Theme {
  const state = (c: string, op: number) => alpha(c, op); // M3 state layers: hover .08, focus .10, press .10
  const focusRing = { outline: `2px solid ${t.secondary}`, outlineOffset: 2 };
  // M3 elevation = tonal surface color; shadows are used only for floating layers (menus, dialogs)
  const shadows = Array.from({ length: 25 }, (_, i) =>
    i < 8 ? "none" : `0 1px 3px 1px ${alpha(t.primary, 0.1)}, 0 2px 8px ${alpha(t.primary, 0.08)}`,
  ) as Shadows;

  return createTheme({
    shape: { borderRadius: shape.md },
    shadows,
    palette: {
      mode: "light",
      primary: { main: t.primary, contrastText: t.onPrimary, light: t.primaryContainer, dark: t.primary },
      secondary: { main: t.secondary, contrastText: t.onSecondary },
      error: { main: t.error, contrastText: t.onError, light: t.errorContainer, dark: t.onErrorContainer },
      warning: { main: t.warning, contrastText: t.onWarning, light: t.warningContainer, dark: t.onWarningContainer },
      success: { main: t.success, contrastText: t.onSuccess, light: t.successContainer, dark: t.onSuccessContainer },
      info: { main: t.primary, contrastText: t.onPrimary, light: t.primaryContainer, dark: t.onPrimaryContainer },
      background: { default: t.background, paper: t.surfaceContainerLowest },
      text: { primary: t.onBackground, secondary: t.onSurfaceVariant },
      divider: t.outlineVariant,
      action: { hover: state(t.primary, 0.08), focus: state(t.primary, 0.1), selected: state(t.primary, 0.12), active: t.onSurfaceVariant },
      tokens: t,
    },
    typography: {
      fontFamily: FONT,
      // M3 type scale roles mapped onto MUI variants
      h1: type(57, 64, 400, -0.25), // display large
      h2: type(45, 52, 400, 0), // display medium
      h3: type(36, 44, 400, 0), // display small / headline large 32
      h4: type(28, 36, 400, 0), // headline medium
      h5: type(24, 32, 400, 0), // headline small
      h6: type(22, 28, 400, 0), // title large
      subtitle1: type(16, 24, 500, 0.15), // title medium
      subtitle2: type(14, 20, 500, 0.1), // title small
      body1: type(16, 24, 400, 0.5), // body large
      body2: type(14, 20, 400, 0.25), // body medium
      button: { ...type(14, 20, 500, 0.1), textTransform: "none" }, // label large
      caption: type(12, 16, 400, 0.4), // body small
      overline: { ...type(11, 16, 500, 0.5), textTransform: "none" }, // label small
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: { backgroundColor: t.background, color: t.onBackground },
          "*:focus-visible": { outline: `2px solid ${t.secondary}`, outlineOffset: 2 },
          "::selection": { background: t.secondaryContainer },
        },
      },
      MuiButtonBase: { defaultProps: { disableRipple: false } },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: { borderRadius: shape.full, padding: "4px 16px", minHeight: 34, fontSize: "0.8125rem", "&.Mui-focusVisible": focusRing },
          sizeSmall: { minHeight: 28, padding: "2px 12px", fontSize: "0.75rem" },
          sizeLarge: { minHeight: 44, padding: "8px 24px", fontSize: "0.875rem" },
          outlined: { borderColor: t.outline },
        },
        variants: [
          {
            props: { variant: "contained", color: "primary" },
            style: { "&:hover": { backgroundColor: t.primary, boxShadow: `inset 0 0 0 100px ${state(t.onPrimary, 0.08)}` } },
          },
          {
            props: { variant: "text" },
            style: { paddingInline: 10 },
          },
          {
            props: { variant: "tonal" },
            style: {
              backgroundColor: t.primaryContainer, color: t.onPrimaryContainer,
              "&:hover": { backgroundColor: t.primaryContainer, boxShadow: `inset 0 0 0 100px ${state(t.onPrimaryContainer, 0.08)}` },
              "&.Mui-disabled": { backgroundColor: alpha(t.onSurface, 0.12), color: alpha(t.onSurface, 0.38) },
            },
          },
        ],
      },
      MuiIconButton: { styleOverrides: { root: { "&.Mui-focusVisible": focusRing } } },
      MuiChip: {
        styleOverrides: {
          root: { borderRadius: shape.sm, fontWeight: 500, height: 28 },
          label: { paddingInline: 12 },
          sizeSmall: { height: 24 },
        },
      },
      MuiTextField: { defaultProps: { variant: "outlined", size: "small", fullWidth: true } },
      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            borderRadius: shape.md, backgroundColor: t.surfaceContainerLowest,
            "& .MuiOutlinedInput-notchedOutline": { borderColor: t.outline },
            "&:hover .MuiOutlinedInput-notchedOutline": { borderColor: t.onSurfaceVariant },
            "&.Mui-focused": { boxShadow: `0 0 0 3px ${alpha(t.secondary, 0.35)}` },
            "&.Mui-focused .MuiOutlinedInput-notchedOutline": { borderColor: t.primary, borderWidth: 2 },
            "&.Mui-disabled": { backgroundColor: t.surfaceContainerLow },
          },
        },
      },
      MuiInputLabel: { styleOverrides: { root: { "&.Mui-focused": { color: t.primary } } } },
      MuiFormHelperText: { styleOverrides: { root: { marginInline: 14 } } },
      MuiCheckbox: { defaultProps: { size: "small" } },
      MuiPaper: { styleOverrides: { root: { backgroundImage: "none" }, rounded: { borderRadius: shape.md } } },
      MuiCard: {
        defaultProps: { variant: "outlined" },
        styleOverrides: { root: { borderRadius: shape.md, borderColor: t.outlineVariant, backgroundColor: t.surfaceContainerLowest } },
      },
      MuiCardActionArea: {
        styleOverrides: {
          root: { "&:hover .MuiCardActionArea-focusHighlight": { opacity: 0.08 }, "&.Mui-focusVisible": focusRing },
          focusHighlight: { backgroundColor: t.primary },
        },
      },
      MuiAppBar: {
        defaultProps: { elevation: 0, color: "inherit" },
        styleOverrides: { root: { backgroundColor: t.surfaceContainerLowest, borderBottom: `1px solid ${t.outlineVariant}`, color: t.onSurface } },
      },
      MuiToolbar: { styleOverrides: { root: { minHeight: 64 } } },
      MuiDrawer: {
        styleOverrides: {
          paper: { backgroundColor: t.surfaceContainerLow, backgroundImage: "none" },
        },
      },
      MuiDialog: { styleOverrides: { paper: { borderRadius: shape.xl, backgroundColor: t.surfaceContainerHigh } } },
      MuiDialogTitle: { styleOverrides: { root: { ...type(24, 32, 400, 0), padding: "24px 24px 16px" } } },
      MuiDialogContent: { styleOverrides: { root: { padding: "8px 24px" } } },
      MuiDialogActions: { styleOverrides: { root: { padding: "16px 24px 24px", gap: 8 } } },
      MuiMenu: { styleOverrides: { paper: { borderRadius: shape.md, backgroundColor: t.surfaceContainer, marginTop: 4 } } },
      MuiMenuItem: { styleOverrides: { root: { borderRadius: shape.sm, marginInline: 6, minHeight: 40, "&.Mui-selected": { backgroundColor: t.secondaryContainer } } } },
      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: shape.full, minHeight: 32, paddingBlock: 2, paddingInline: 14, flexGrow: 0, flexShrink: 0,
            "&.Mui-selected": { backgroundColor: t.secondaryContainer, color: t.onSecondaryContainer, "&:hover": { backgroundColor: t.secondaryContainer } },
            "&.Mui-focusVisible": focusRing,
          },
        },
      },
      MuiAlert: {
        defaultProps: { variant: "standard" },
        styleOverrides: {
          root: { borderRadius: shape.md, alignItems: "center" },
        },
        variants: [
          { props: { severity: "error", variant: "standard" }, style: { backgroundColor: t.errorContainer, color: t.onErrorContainer } },
          { props: { severity: "warning", variant: "standard" }, style: { backgroundColor: t.warningContainer, color: t.onWarningContainer } },
          { props: { severity: "success", variant: "standard" }, style: { backgroundColor: t.successContainer, color: t.onSuccessContainer } },
          { props: { severity: "info", variant: "standard" }, style: { backgroundColor: t.primaryContainer, color: t.onPrimaryContainer } },
        ],
      },
      MuiSnackbarContent: { styleOverrides: { root: { borderRadius: shape.sm, backgroundColor: t.onSurface, color: t.surface } } },
      MuiLinearProgress: {
        styleOverrides: {
          root: { height: 4, borderRadius: shape.full, backgroundColor: t.secondaryContainer },
          bar: { backgroundColor: t.secondary },
        },
      },
      MuiCircularProgress: { defaultProps: { color: "secondary" } },
      MuiTooltip: { styleOverrides: { tooltip: { borderRadius: shape.sm, backgroundColor: t.onSurface, ...type(12, 16, 500, 0.5) } } },
      MuiTableCell: {
        styleOverrides: {
          root: { borderColor: t.outlineVariant, padding: "6px 12px" },
          head: { backgroundColor: t.surfaceContainer, fontWeight: 500, color: t.onSurfaceVariant },
        },
      },
      MuiTab: { styleOverrides: { root: { ...type(14, 20, 500, 0.1), textTransform: "none", minHeight: 48 } } },
      MuiTabs: { styleOverrides: { indicator: { height: 3, borderRadius: "3px 3px 0 0", backgroundColor: t.secondary } } },
      MuiSwitch: { styleOverrides: { switchBase: { "&.Mui-checked + .MuiSwitch-track": { backgroundColor: t.secondary } } } },
    },
  });
}

export const theme = buildTheme(lightTokens);
