import { Typography } from "@mui/material";
import { ApiError, type GateInfo } from "../api";
import { ConfirmDialog, monoSx } from "../ui";

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "unknown size";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

/** The 409 from POST /query (gate=true) means "ask first": returns its estimate, else null. */
export function gateFrom(err: unknown): GateInfo | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const d = err.detail as Partial<GateInfo> | undefined;
  return d?.needs_confirmation ? (d as GateInfo) : null;
}

/** Confirm a run whose source is a view or whose dry-run estimate is large. Confirming is audit-logged server-side. */
export function GateDialog({ info, title = "Run this query?", confirmLabel = "Run anyway", onConfirm, onCancel }: {
  info: GateInfo | null; title?: string; confirmLabel?: string; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <ConfirmDialog open={!!info} title={title} confirmLabel={confirmLabel} onConfirm={onConfirm} onCancel={onCancel}
      body={info && (
        <>
          <Typography variant="body2" sx={{ mb: 1 }}>Estimated scan: <strong>{fmtBytes(info.estimated_bytes)}</strong>. This is a dry-run estimate; nothing has run yet.</Typography>
          {info.reasons.map((r) => <Typography key={r} variant="body2">• {r}</Typography>)}
          {info.base_tables.length > 0 && (
            <Typography variant="body2" sx={{ mt: 1 }}>Base tables:{" "}
              <Typography component="span" variant="caption" sx={monoSx}>{info.base_tables.join(", ")}</Typography>
            </Typography>
          )}
          <Typography variant="body2" sx={{ mt: 1 }} color="text.secondary">{info.suggestion} Your confirmation is recorded in the audit log.</Typography>
        </>
      )} />
  );
}
