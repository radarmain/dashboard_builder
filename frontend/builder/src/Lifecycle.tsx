// Favorites star, archive / restore / permanent-delete menu + dialogs, archived banner, and a tiny
// snackbar store. Permissions mirror the backend (which is the real gate): clients are admin-only;
// project/view archive+restore are admin or analyst; permanent delete is admin-only.
import { useEffect, useState, useSyncExternalStore } from "react";
import { Alert, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Menu, MenuItem, Snackbar, Switch, TextField, Tooltip, Typography } from "@mui/material";
import { Icon, useAuth } from "@rdb/core";
import { del, get, post, type DeletePreview, type FavRef, type Lifecycle } from "./api";
import { isFavorite, ensureFavorites, setFavorite, getShowArchived, setShowArchived, useNavCache } from "./navTree";
import { Btn, ErrorBox, errMsg, monoSx } from "./ui";

export type Kind = "client" | "project" | "view";
export interface ItemRef { kind: Kind; client: string; project?: string; view?: string }

export const itemPath = (r: ItemRef) =>
  `/clients/${r.client}` + (r.project ? `/projects/${r.project}` : "") + (r.view ? `/views/${r.view}` : "");
const slugOf = (r: ItemRef) => (r.view ?? r.project ?? r.client);
const favRef = (r: ItemRef): FavRef => ({ type: r.kind, client: r.client, project: r.project, view: r.view });
/** Where to go when the item no longer exists. */
export const parentRoute = (r: ItemRef) => (r.kind === "view" ? `/c/${r.client}/p/${r.project}` : r.kind === "project" ? `/c/${r.client}` : "/");

// ---- snackbar ----
let message: string | null = null;
const subs = new Set<() => void>();
export function notify(m: string | null) { message = m; subs.forEach((f) => f()); }
export function Notifier() {
  const m = useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => message);
  return <Snackbar open={!!m} autoHideDuration={4000} onClose={(_, r) => r !== "clickaway" && notify(null)} message={m}
    anchorOrigin={{ vertical: "bottom", horizontal: "center" }} />;
}

export function useIsAdmin(): boolean {
  return !!useAuth().identity?.groups.includes("admin");
}

// ---- favorites star ----
export function FavStar({ item, name, path, always, size = 14 }: { item: ItemRef; name: string; path: string; always?: boolean; size?: number }) {
  useNavCache();
  useEffect(() => { ensureFavorites(); }, []);
  const ref = favRef(item);
  const on = isFavorite(ref);
  return (
    <Tooltip title={on ? "Remove from favorites" : "Add to favorites"}>
      <IconButton
        size="small" aria-label={on ? `Remove ${name} from favorites` : `Add ${name} to favorites`} aria-pressed={on}
        className="rdb-star" tabIndex={always ? 0 : -1}
        onClick={(e) => {
          e.preventDefault(); e.stopPropagation();
          setFavorite(ref, !on, { name, path }).catch((err) => notify(errMsg(err)));
        }}
        sx={(t) => ({
          p: 0.5, color: on ? t.palette.secondary.main : "text.secondary",
          ...(always || on ? {} : { opacity: 0, "&:focus-visible": { opacity: 1 } }),
        })}
      >
        <Icon name={on ? "favorite-on" : "favorite"} size={size} />
      </IconButton>
    </Tooltip>
  );
}

// ---- "Show archived" switch ----
export function ShowArchivedToggle() {
  useNavCache();
  return (
    <FormControlLabel
      control={<Switch size="small" checked={getShowArchived()} onChange={(e) => setShowArchived(e.target.checked)} />}
      label={<Typography variant="caption">Show archived</Typography>} sx={{ m: 0 }}
    />
  );
}

export function ArchivedBanner({ kind }: { kind: Kind }) {
  return (
    <Alert severity="warning" icon={<Icon name="archive" />} sx={{ mb: 2 }}>
      This {kind} is archived and hidden from clients. Restore it to edit.
    </Alert>
  );
}

// ---- archive / restore / delete ----
const fmtMB = (b: number) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

function archiveText(kind: Kind, publishedViews: boolean) {
  if (kind === "client") return "Clients will immediately lose access to this client and its dashboards. You can restore it later.";
  if (kind === "project") return "Published views in this project stop being visible to clients immediately. You can restore it later.";
  return publishedViews
    ? "The published version stops being visible to clients immediately. You can restore it later."
    : "This view will be hidden. You can restore it later.";
}

function DeleteDialog({ item, name, onClose, onDeleted }: { item: ItemRef; name: string; onClose: () => void; onDeleted: () => void }) {
  const [prev, setPrev] = useState<DeletePreview | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slug = slugOf(item);
  useEffect(() => { get<DeletePreview>(`${itemPath(item)}/delete-preview`).then(setPrev).catch((e) => setError(errMsg(e))); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function go() {
    setBusy(true); setError(null);
    try { await del(`${itemPath(item)}?confirm=${encodeURIComponent(slug)}`); onDeleted(); } catch (e) { setError(errMsg(e)); setBusy(false); }
  }
  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Delete {item.kind} permanently?</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 1.5 }}>
        <Typography variant="body2">
          This permanently deletes <b>{name}</b>{prev ? <> with{" "}
            {item.kind === "client" && <>{prev.projects} project(s), </>}
            {item.kind !== "view" && <>{prev.views} view(s), </>}
            {prev.revisions} revision(s) and {prev.s3_objects} stored file(s) ({fmtMB(prev.s3_bytes)})</> : null}.
        </Typography>
        {prev && item.kind === "client" && (
          <Typography variant="body2">{prev.client_users} client user(s) will lose access. Their accounts stay active.</Typography>
        )}
        <Alert severity="error">
          This can't be undone from the app. An admin can recover files from S3 version history for 30 days.
        </Alert>
        <TextField size="small" label={`Type ${slug} to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)}
          autoComplete="off" slotProps={{ input: { sx: monoSx } }} />
        {!prev && !error && <Typography variant="caption" color="text.secondary">Counting…</Typography>}
        <ErrorBox error={error} />
      </DialogContent>
      <DialogActions>
        <Btn variant="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <Btn color="error" disabled={busy || !prev || typed !== slug} onClick={() => void go()}>{busy ? "Deleting…" : "Delete permanently"}</Btn>
      </DialogActions>
    </Dialog>
  );
}

/** Overflow menu for an item. `lifecycle` is the item's OWN state (restore only makes sense for it). */
export function LifecycleMenu({ item, name, lifecycle, publishedViews = false, onChanged, onDeleted }: {
  item: ItemRef; name: string; lifecycle: Lifecycle; publishedViews?: boolean; onChanged: () => void; onDeleted: () => void;
}) {
  const admin = useIsAdmin();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [dlg, setDlg] = useState<"archive" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const archived = lifecycle === "archived";
  const canArchive = item.kind === "client" ? admin : true;
  if (!(canArchive || (archived && admin))) return null;
  const path = itemPath(item);

  async function act(kind: "archive" | "restore") {
    setBusy(true); setError(null);
    try {
      await post(`${path}/${kind}`);
      notify(kind === "archive" ? `Archived ${name}` : `Restored ${name}`);
      setDlg(null); onChanged();
    } catch (e) { setError(errMsg(e)); if (kind === "restore") notify(errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <>
      <Tooltip title="More actions">
        <IconButton aria-label={`More actions for ${name}`} onClick={(e) => setAnchor(e.currentTarget)}><Icon name="overflow" /></IconButton>
      </Tooltip>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {!archived && <MenuItem onClick={() => { setAnchor(null); setError(null); setDlg("archive"); }}><Icon name="archive" />&nbsp;Archive</MenuItem>}
        {archived && <MenuItem onClick={() => { setAnchor(null); void act("restore"); }}><Icon name="restore" />&nbsp;Restore</MenuItem>}
        {archived && admin && <MenuItem onClick={() => { setAnchor(null); setDlg("delete"); }} sx={{ color: "error.main" }}><Icon name="delete" />&nbsp;Delete permanently…</MenuItem>}
      </Menu>
      {dlg === "archive" && (
        <Dialog open onClose={busy ? undefined : () => setDlg(null)} maxWidth="xs" fullWidth>
          <DialogTitle>Archive {item.kind}?</DialogTitle>
          <DialogContent sx={{ display: "grid", gap: 1.5 }}>
            <Typography variant="body2"><b>{name}</b>: {archiveText(item.kind, publishedViews)}</Typography>
            <ErrorBox error={error} />
          </DialogContent>
          <DialogActions>
            <Btn variant="ghost" onClick={() => setDlg(null)} disabled={busy}>Cancel</Btn>
            <Btn disabled={busy} onClick={() => void act("archive")}>{busy ? "Archiving…" : "Archive"}</Btn>
          </DialogActions>
        </Dialog>
      )}
      {dlg === "delete" && (
        <DeleteDialog item={item} name={name} onClose={() => setDlg(null)}
          onDeleted={() => { setDlg(null); notify(`Deleted ${name}`); onDeleted(); }} />
      )}
    </>
  );
}
