import { useCallback, useEffect, useState } from "react";
import {
  Box, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Tab, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Tabs, TextField, Typography,
} from "@mui/material";
import { Navigate } from "react-router-dom";
import { useAuth } from "@rdb/core";
import { get, post } from "../api";
import { Btn, ConfirmDialog, ErrorBox, Loading, PageHeader, errMsg, monoSx } from "../ui";
import { EmptyRow, RowMenu, StatusChip, fmtDay, type UserStatus } from "./UserParts";

type Role = "admin" | "analyst" | "client";
interface AdminUser { sub: string; email: string; name: string | null; role: Role; status: UserStatus; created_at: string; clients?: string[] }
interface Page { users: AdminUser[]; next_cursor: string | null; total: number }

const ROLE_LABEL: Record<Role, string> = { admin: "Admin", analyst: "Analyst", client: "Client" };
const FILTERS: { key: "" | Role; label: string }[] = [
  { key: "", label: "All" }, { key: "admin", label: "Admins" }, { key: "analyst", label: "Analysts" }, { key: "client", label: "Clients" },
];

function AddStaffDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "analyst">("analyst");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await post("/admin/users", { email: email.trim(), role }); onDone(); } catch (err) { setError(errMsg(err)); setBusy(false); }
  }
  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth slotProps={{ paper: { component: "form", onSubmit: submit } }}>
      <DialogTitle>Add user</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2.5, pt: "8px !important" }}>
        <TextField select label="Role" value={role} onChange={(e) => setRole(e.target.value as "admin" | "analyst")}
          helperText="Client users are added from each client's Settings tab.">
          <MenuItem value="admin">Admin</MenuItem>
          <MenuItem value="analyst">Analyst</MenuItem>
        </TextField>
        <TextField label="Email" type="email" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
        <ErrorBox error={error} />
      </DialogContent>
      <DialogActions>
        <Btn type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <Btn type="submit" disabled={busy || !email.trim()}>{busy ? "Adding…" : "Add user"}</Btn>
      </DialogActions>
    </Dialog>
  );
}

function ChangeRoleDialog({ user, onClose, onDone }: { user: AdminUser; onClose: () => void; onDone: () => void }) {
  const [role, setRole] = useState<"admin" | "analyst">(user.role === "admin" ? "analyst" : "admin");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await post(`/admin/users/${user.sub}/role`, { role }); onDone(); } catch (err) { setError(errMsg(err)); setBusy(false); }
  }
  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth slotProps={{ paper: { component: "form", onSubmit: submit } }}>
      <DialogTitle>Change role</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2.5, pt: "8px !important" }}>
        <Typography variant="body2" color="text.secondary">{user.email}</Typography>
        <TextField select label="Role" value={role} onChange={(e) => setRole(e.target.value as "admin" | "analyst")}>
          <MenuItem value="admin">Admin</MenuItem>
          <MenuItem value="analyst">Analyst</MenuItem>
        </TextField>
        <ErrorBox error={error} />
      </DialogContent>
      <DialogActions>
        <Btn type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <Btn type="submit" disabled={busy || role === user.role}>{busy ? "Saving…" : "Change role"}</Btn>
      </DialogActions>
    </Dialog>
  );
}

function UsersTab({ me }: { me: string | undefined }) {
  const [role, setRole] = useState<"" | Role>("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<AdminUser[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [roleFor, setRoleFor] = useState<AdminUser | null>(null);
  const [toggle, setToggle] = useState<AdminUser | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { const t = setTimeout(() => setQ(search), 300); return () => clearTimeout(t); }, [search]);

  const load = useCallback((cursor?: string) => {
    const qs = new URLSearchParams({ limit: "25" });
    if (role) qs.set("role", role);
    if (q) qs.set("search", q);
    if (cursor) qs.set("cursor", cursor);
    get<Page>(`/admin/users?${qs}`).then((p) => {
      setRows((r) => (cursor && r ? [...r, ...p.users] : p.users));
      setNext(p.next_cursor);
    }).catch((e) => setError(errMsg(e)));
  }, [role, q]);
  useEffect(() => { setRows(null); setError(null); load(); }, [load]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setError(null);
    setNotice(null);
    try { await fn(); if (ok) setNotice(ok); load(); } catch (e) { setError(errMsg(e)); }
  }

  return (
    <Box>
      <Box sx={{ display: "flex", gap: 2, alignItems: "center", flexWrap: "wrap", mb: 2 }}>
        <Box sx={{ display: "flex", gap: 1 }}>
          {FILTERS.map((f) => (
            <Chip key={f.key} label={f.label} clickable color={role === f.key ? "primary" : "default"}
              variant={role === f.key ? "filled" : "outlined"} onClick={() => setRole(f.key)} />
          ))}
        </Box>
        <TextField size="small" placeholder="Search email or name" value={search} onChange={(e) => setSearch(e.target.value)} sx={{ minWidth: 260 }} />
        <Box sx={{ ml: "auto" }}><Btn onClick={() => setAdding(true)}>Add user</Btn></Box>
      </Box>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {notice && <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{notice}</Typography>}
      {rows === null && !error && <Loading />}
      {rows && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Email</TableCell><TableCell>Role</TableCell><TableCell>Status</TableCell><TableCell>Created</TableCell>
                <TableCell>Clients</TableCell><TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((u) => (
                <TableRow key={u.sub} sx={{ opacity: u.status === "disabled" ? 0.55 : 1 }}>
                  <TableCell>{u.email}{u.email === me ? " (you)" : ""}</TableCell>
                  <TableCell>{ROLE_LABEL[u.role] ?? u.role}</TableCell>
                  <TableCell><StatusChip status={u.status} /></TableCell>
                  <TableCell>{fmtDay(u.created_at)}</TableCell>
                  <TableCell>
                    <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
                      {u.clients?.map((c) => <Chip key={c} size="small" label={c} sx={{ ...monoSx, height: 22 }} />)}
                    </Box>
                  </TableCell>
                  <TableCell align="right">
                    <RowMenu label={`Actions for ${u.email}`} items={[
                      { label: "Change role", hidden: u.role === "client" || u.email === me, onClick: () => setRoleFor(u) },
                      { label: u.status === "disabled" ? "Enable" : "Disable", hidden: u.status !== "disabled" && u.email === me, onClick: () => setToggle(u) },
                      { label: "Resend invite", hidden: u.status !== "invited", onClick: () => void run(() => post(`/admin/users/${u.sub}/resend-invite`), `Invite re-sent to ${u.email}.`) },
                    ]} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {rows.length === 0 && <EmptyRow>No users match.</EmptyRow>}
        </TableContainer>
      )}
      {next && <Box sx={{ mt: 2 }}><Btn variant="secondary" onClick={() => load(next)}>Load more</Btn></Box>}
      {adding && <AddStaffDialog onClose={() => setAdding(false)} onDone={() => { setAdding(false); load(); }} />}
      {roleFor && <ChangeRoleDialog user={roleFor} onClose={() => setRoleFor(null)} onDone={() => { setRoleFor(null); load(); }} />}
      <ConfirmDialog
        open={!!toggle}
        title={toggle?.status === "disabled" ? "Enable user?" : "Disable user?"}
        confirmLabel={toggle?.status === "disabled" ? "Enable" : "Disable"}
        destructive={toggle?.status !== "disabled"}
        body={toggle?.status === "disabled"
          ? `${toggle?.email} will be able to sign in again.`
          : `${toggle?.email} will be signed out everywhere and can't sign in until re-enabled. Their access and data are kept.`}
        onCancel={() => setToggle(null)}
        onConfirm={() => {
          const u = toggle; setToggle(null);
          if (u) void run(() => post(`/admin/users/${u.sub}/${u.status === "disabled" ? "enable" : "disable"}`));
        }}
      />
    </Box>
  );
}

const TABS = [{ key: "users", label: "Users" }]; // more tabs later

/** Admin-only /settings. Guarded here too (the nav item is hidden for analysts as well). */
export function AdminSettingsPage() {
  const { identity } = useAuth();
  const [tab, setTab] = useState("users");
  if (!identity?.groups.includes("admin")) return <Navigate to="/" replace />;
  return (
    <Box sx={{ p: 4 }}>
      <PageHeader title="Settings" subtitle="Manage users and roles" />
      <Tabs value={tab} onChange={(_, v: string) => setTab(v)} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
        {TABS.map((t) => <Tab key={t.key} value={t.key} label={t.label} />)}
      </Tabs>
      {tab === "users" && <UsersTab me={identity.email.toLowerCase()} />}
    </Box>
  );
}
