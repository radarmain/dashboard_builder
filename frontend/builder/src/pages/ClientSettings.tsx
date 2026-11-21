import { useCallback, useEffect, useState } from "react";
import {
  Box, Dialog, DialogActions, DialogContent, DialogTitle, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, Typography,
} from "@mui/material";
import { publicClientUrl } from "@rdb/portal";
import { del, get, post } from "../api";
import { Btn, ConfirmDialog, ErrorBox, Loading, errMsg, monoSx } from "../ui";
import { CopyLinkButton } from "../Preview";
import { EmptyRow, RowMenu, StatusChip, fmtDay, type UserStatus } from "./UserParts";

interface ClientUser { sub: string; email: string; status: UserStatus; added_at: string; added_by: string }

function AddUserDialog({ client, onClose, onDone }: { client: string; onClose: () => void; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post(`/clients/${client}/users`, { email: email.trim(), name: name.trim() || undefined });
      onDone();
    } catch (err) { setError(errMsg(err)); setBusy(false); }
  }
  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth slotProps={{ paper: { component: "form", onSubmit: submit } }}>
      <DialogTitle>Add user</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2.5, pt: "8px !important" }}>
        <TextField label="Email" type="email" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)}
          helperText="They'll get an email with a temporary password. Send them the client link below." />
        <TextField label="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        <ErrorBox error={error} />
      </DialogContent>
      <DialogActions>
        <Btn type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <Btn type="submit" disabled={busy || !email.trim()}>{busy ? "Adding…" : "Add user"}</Btn>
      </DialogActions>
    </Dialog>
  );
}

/** Settings tab of the Client page. Sections stack here; Users is the first. */
export function ClientSettings({ client, clientName }: { client: string; clientName: string }) {
  return (
    <Box sx={{ display: "grid", gap: 4 }}>
      <UsersSection client={client} clientName={clientName} />
    </Box>
  );
}

function UsersSection({ client, clientName }: { client: string; clientName: string }) {
  const [users, setUsers] = useState<ClientUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<ClientUser | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const url = publicClientUrl(client);

  const load = useCallback(() => {
    get<ClientUser[]>(`/clients/${client}/users`).then(setUsers).catch((e) => setError(errMsg(e)));
  }, [client]);
  useEffect(load, [load]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setError(null);
    setNotice(null);
    try { await fn(); if (ok) setNotice(ok); load(); } catch (e) { setError(errMsg(e)); }
  }

  return (
    <Box component="section">
      <Box sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 2, mb: 2 }}>
        <Box>
          <Typography variant="h6" component="h2">Users</Typography>
          <Typography variant="body2" color="text.secondary">People who can sign in and view this client's published dashboards.</Typography>
        </Box>
        <Btn onClick={() => setAdding(true)}>Add user</Btn>
      </Box>
      <Paper variant="outlined" sx={{ p: 2, mb: 2, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2, flexWrap: "wrap" }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="caption" color="text.secondary">Client link</Typography>
          <Typography sx={{ ...monoSx, wordBreak: "break-all" }}>{url}</Typography>
          <Typography variant="caption" color="text.secondary">The client must have at least one published view.</Typography>
        </Box>
        <CopyLinkButton label="Copy link" url={url} />
      </Paper>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {notice && <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{notice}</Typography>}
      {users === null && !error && <Loading />}
      {users && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Email</TableCell><TableCell>Status</TableCell><TableCell>Added</TableCell><TableCell align="right" /></TableRow>
            </TableHead>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.sub} sx={{ opacity: u.status === "disabled" ? 0.55 : 1 }}>
                  <TableCell>{u.email}</TableCell>
                  <TableCell><StatusChip status={u.status} /></TableCell>
                  <TableCell>{fmtDay(u.added_at)}</TableCell>
                  <TableCell align="right">
                    <RowMenu label={`Actions for ${u.email}`} items={[
                      { label: "Resend invite", hidden: u.status !== "invited", onClick: () => void run(() => post(`/clients/${client}/users/${u.sub}/resend-invite`), `Invite re-sent to ${u.email}.`) },
                      { label: "Remove access", onClick: () => setRemoving(u) },
                    ]} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {users.length === 0 && <EmptyRow>No users yet. Add one to give them access.</EmptyRow>}
        </TableContainer>
      )}
      {adding && <AddUserDialog client={client} onClose={() => setAdding(false)} onDone={() => { setAdding(false); load(); }} />}
      <ConfirmDialog
        open={!!removing} title="Remove access?" confirmLabel="Remove access" destructive
        body={`Removes access to ${clientName || client} only. The account stays active for any other clients.`}
        onCancel={() => setRemoving(null)}
        onConfirm={() => { const u = removing; setRemoving(null); if (u) void run(() => del(`/clients/${client}/users/${u.sub}`)); }}
      />
    </Box>
  );
}
