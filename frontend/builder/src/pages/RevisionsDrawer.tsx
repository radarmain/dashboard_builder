import { useEffect, useState } from "react";
import { Box, Drawer, IconButton, List, ListItem, Typography } from "@mui/material";
import { get, post, type Revision } from "../api";
import { Badge, Btn, Card, ConfirmDialog, ErrorBox, Loading, errMsg } from "../ui";

export function RevisionsDrawer({ viewPath, dirty, onClose, onRestored }: {
  viewPath: string; dirty: boolean; onClose: () => void; onRestored: () => Promise<void>;
}) {
  const [revs, setRevs] = useState<Revision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);

  useEffect(() => {
    get<Revision[]>(`${viewPath}/revisions`).then(setRevs).catch((e) => setError(errMsg(e)));
  }, [viewPath]);

  async function doRestore(n: number) {
    setConfirm(null);
    setBusy(n); setError(null);
    try {
      await post(`${viewPath}/revisions/${n}/restore`);
      await onRestored();
    } catch (e) { setError(errMsg(e)); setBusy(null); }
  }
  const restore = (n: number) => (dirty ? setConfirm(n) : void doRestore(n));

  return (
    <>
      <Drawer anchor="right" open onClose={onClose} slotProps={{ paper: { sx: { width: 400, maxWidth: "100vw", p: 3, borderRadius: "16px 0 0 16px" } } }}>
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
          <Typography variant="h5" component="h2">Revisions</Typography>
          <IconButton onClick={onClose} aria-label="Close revisions">✕</IconButton>
        </Box>
        <Typography variant="caption" sx={{ color: "text.secondary", mb: 2, display: "block" }}>
          The last 10 saves. Restoring creates a new draft revision; old revisions are never changed.
        </Typography>
        <ErrorBox error={error} />
        {!revs && !error && <Loading />}
        {revs?.length === 0 && <Typography color="text.secondary">No revisions yet. Save to create one.</Typography>}
        <List disablePadding sx={{ display: "grid", gap: 1.5 }}>
          {revs?.map((r) => (
            <ListItem key={r.revision} disablePadding>
              <Card sx={{ width: "100%", p: 2 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="subtitle1">#{r.revision}</Typography>
                  {r.is_published && <Badge tone="green">Published</Badge>}
                  {r.is_draft && <Badge tone="blue">Current draft</Badge>}
                </Box>
                <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.5 }}>
                  {r.updated_at ? new Date(r.updated_at).toLocaleString() : "unknown time"} · {r.updated_by ?? "unknown"}
                </Typography>
                <Btn variant="secondary" size="small" sx={{ mt: 1.5 }} disabled={busy !== null || r.is_draft} onClick={() => restore(r.revision)}>
                  {busy === r.revision ? "Restoring…" : "Restore as draft"}
                </Btn>
              </Card>
            </ListItem>
          ))}
        </List>
      </Drawer>
      <ConfirmDialog open={confirm !== null} title="Restore this revision?" confirmLabel="Restore"
        body="Restoring replaces your unsaved edits." onConfirm={() => confirm !== null && void doRestore(confirm)} onCancel={() => setConfirm(null)} />
    </>
  );
}
