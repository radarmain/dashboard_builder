import type { ReactNode } from "react";
import {
  Alert, Box, Button, Card as MuiCard, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogContentText,
  DialogTitle, Typography, type ButtonProps, type SxProps, type Theme,
} from "@mui/material";
import { MONO } from "./theme";

/** Full-color RADaR wordmark, copied from frontend/core/brand/ into each app's public/ (RADaR-Logo.png, 1024px wide). */
export const LOGO = "/rdb-logo.png";

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const monoSx = { fontFamily: MONO };

/** primary = filled, secondary = tonal, ghost = text (M3 button hierarchy). */
export function Btn({ variant = "primary", ...rest }: Omit<ButtonProps, "variant"> & { variant?: "primary" | "secondary" | "ghost" }) {
  const v = { primary: "contained", secondary: "tonal", ghost: "text" }[variant] as ButtonProps["variant"];
  return <Button variant={v} color="primary" {...rest} />;
}

export function ErrorBox({ error, onClose }: { error: string | null | undefined; onClose?: () => void }) {
  if (!error) return null;
  return (
    <Alert severity="error" onClose={onClose} sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {error}
    </Alert>
  );
}

export type Tone = "gray" | "green" | "amber" | "blue";
/** Status chip (tonal M3 assist chip). */
export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <Chip
      size="small"
      label={children}
      sx={(t) => {
        const k = t.palette.tokens;
        return {
          gray: { bgcolor: k.surfaceContainerHigh, color: k.onSurfaceVariant },
          green: { bgcolor: k.successContainer, color: k.onSuccessContainer },
          amber: { bgcolor: k.warningContainer, color: k.onWarningContainer },
          blue: { bgcolor: k.primaryContainer, color: k.onPrimaryContainer },
        }[tone];
      }}
    />
  );
}

export function Card({ children, sx }: { children: ReactNode; sx?: SxProps<Theme> }) {
  return <MuiCard sx={[{ p: 2.5 }, ...(Array.isArray(sx) ? sx : [sx])]}>{children}</MuiCard>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, color: "text.secondary", py: 2 }}>
      <CircularProgress size={20} />
      <Typography variant="body2">{label}</Typography>
    </Box>
  );
}

export function PageHeader({ title, subtitle, action }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <Box sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 2, mb: 3 }}>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="h4" component="h1" sx={{ color: "text.primary" }}>{title}</Typography>
        {subtitle && <Typography variant="body2" sx={{ color: "text.secondary", mt: 0.5 }}>{subtitle}</Typography>}
      </Box>
      {action}
    </Box>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel = "Confirm", destructive, onConfirm, onCancel }: {
  open: boolean; title: string; body: ReactNode; confirmLabel?: string; destructive?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Dialog open={open} onClose={onCancel} maxWidth="xs" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent><DialogContentText component="div">{body}</DialogContentText></DialogContent>
      <DialogActions>
        <Btn variant="ghost" onClick={onCancel}>Cancel</Btn>
        <Btn color={destructive ? "error" : "primary"} onClick={onConfirm}>{confirmLabel}</Btn>
      </DialogActions>
    </Dialog>
  );
}
