import { useState } from "react";
import { Box, IconButton, Menu, MenuItem, Typography } from "@mui/material";
import { Badge } from "../ui";

export type UserStatus = "invited" | "active" | "disabled";

export function StatusChip({ status }: { status: UserStatus }) {
  const tone = { invited: "amber", active: "green", disabled: "gray" }[status] as "amber" | "green" | "gray";
  return <Badge tone={tone}>{status[0].toUpperCase() + status.slice(1)}</Badge>;
}

export const fmtDay = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";

/** "⋯" row menu. Items with `hidden` are omitted; renders nothing when no items remain. */
export function RowMenu({ items, label }: { items: { label: string; onClick: () => void; hidden?: boolean }[]; label: string }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const shown = items.filter((i) => !i.hidden);
  if (shown.length === 0) return null;
  return (
    <>
      <IconButton size="small" aria-label={label} aria-haspopup="menu" onClick={(e) => setAnchor(e.currentTarget)}>
        <Box component="span" aria-hidden sx={{ fontSize: 20, lineHeight: 1 }}>⋯</Box>
      </IconButton>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
        {shown.map((i) => (
          <MenuItem key={i.label} onClick={() => { setAnchor(null); i.onClick(); }}>{i.label}</MenuItem>
        ))}
      </Menu>
    </>
  );
}

export function EmptyRow({ children }: { children: string }) {
  return <Typography color="text.secondary" sx={{ py: 3, textAlign: "center" }}>{children}</Typography>;
}
