import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Box, CardActionArea, Tab, Tabs, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from "@mui/material";
import { get, post, type Client, type Project } from "../api";
import { publicClientUrl } from "@rdb/portal";
import { ClientPreviewButton, CopyLinkButton, PREVIEW_BASE } from "../Preview";
import { ClientSettings } from "./ClientSettings";
import { Btn, Card, ErrorBox, Loading, PageHeader, SLUG_RE, errMsg, monoSx, slugify } from "../ui";

/** Dialog: name + auto-suggested, editable, validated slug. */
function NameSlugForm({ label, onSubmit, onCancel, withDescription, slugRule }: {
  /** Extra slug rule (returns an error message or null), e.g. client subdomain rules. */
  slugRule?: (slug: string) => string | null;
  label: string; onSubmit: (name: string, slug: string, description: string) => Promise<void>; onCancel: () => void;
  /** Optional multiline description (max 300 chars), shown to clients on view cards. */
  withDescription?: boolean;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [slug, setSlug] = useState("");
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ruleError = slug ? (slugRule?.(slug) ?? null) : null;
  const slugOk = SLUG_RE.test(slug) && slug.length <= 63 && !ruleError;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await onSubmit(name.trim(), slug, description.trim()); } catch (err) { setError(errMsg(err)); setBusy(false); }
  }
  return (
    <Dialog open onClose={busy ? undefined : onCancel} maxWidth="xs" fullWidth slotProps={{ paper: { component: "form", onSubmit: submit } }}>
      <DialogTitle>New {label.toLowerCase()}</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2.5, pt: "8px !important" }}>
        <TextField label={`${label} name`} autoFocus value={name}
          onChange={(e) => { setName(e.target.value); if (!touched) setSlug(slugify(e.target.value)); }} />
        <TextField label="Slug" value={slug} error={!!slug && !slugOk}
          helperText={ruleError ?? (slug && !slugOk ? "Slug must match ^[a-z0-9]+(-[a-z0-9]+)*$" : "Lowercase letters, numbers and hyphens. Cannot be changed later.")}
          slotProps={{ input: { sx: monoSx } }}
          onChange={(e) => { setTouched(true); setSlug(e.target.value); }} />
        {withDescription && (
          <TextField label="Description (optional)" multiline minRows={2} maxRows={5} value={description}
            onChange={(e) => setDescription(e.target.value.slice(0, 300))}
            helperText={`Visible to clients on the dashboard card. ${description.length}/300`} />
        )}
        <ErrorBox error={error} />
      </DialogContent>
      <DialogActions>
        <Btn type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Btn>
        <Btn type="submit" disabled={busy || !name.trim() || !slugOk}>{busy ? "Creating…" : "Create"}</Btn>
      </DialogActions>
    </Dialog>
  );
}

/** One clickable list card (name + mono subtitle + optional trailing node). */
export function LinkCard({ to, title, subtitle, description, trailing }: { to: string; title: string; subtitle: string; description?: string; trailing?: React.ReactNode }) {
  return (
    <Card sx={{ p: 0 }}>
      <CardActionArea component={Link} to={to} sx={{ p: 2.5, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2 }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle1" noWrap>{title}</Typography>
          <Typography variant="caption" noWrap sx={{ ...monoSx, color: "text.secondary", display: "block" }}>{subtitle}</Typography>
          {description && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{description}</Typography>
          )}
        </Box>
        {trailing}
      </CardActionArea>
    </Card>
  );
}

const gridSx = { display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "repeat(2, 1fr)", xl: "repeat(3, 1fr)" } };

export function ClientsPage() {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [rules, setRules] = useState<{ reserved: string[]; client_slug_max: number } | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    get<Client[]>("/clients").then(setClients).catch((e) => setError(errMsg(e)));
    get<{ reserved: string[]; client_slug_max: number }>("/slug-rules").then(setRules).catch(() => {});
  }, []);

  // Client slugs become <slug>.radar-analytics.com, so they follow the backend's subdomain rules.
  const clientSlugRule = (s: string) =>
    rules && s.length > rules.client_slug_max ? `At most ${rules.client_slug_max} characters.`
    : rules?.reserved.includes(s) ? `"${s}" is reserved (used by the site or by email/DNS). Pick another.`
    : null;

  return (
    <Box sx={{ p: 4 }}>
      <PageHeader title="Clients" action={<Btn onClick={() => setAdding(true)}>Add client</Btn>} />
      {adding && (
        <NameSlugForm label="Client" slugRule={clientSlugRule} onCancel={() => setAdding(false)}
          onSubmit={async (name, slug) => { await post("/clients", { name, slug }); nav(`/c/${slug}`); }} />
      )}
      <ErrorBox error={error} />
      {clients === null && !error && <Loading />}
      {clients?.length === 0 && <Typography color="text.secondary">No clients yet.</Typography>}
      <Box sx={gridSx}>
        {clients?.map((c) => <LinkCard key={c.slug} to={`/c/${c.slug}`} title={c.name} subtitle={c.slug} />)}
      </Box>
    </Box>
  );
}

export function ClientPage() {
  const { client = "" } = useParams();
  const [info, setInfo] = useState<Client | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "settings" ? "settings" : "projects";
  const nav = useNavigate();

  useEffect(() => {
    get<Client>(`/clients/${client}`).then(setInfo).catch((e) => setError(errMsg(e)));
    get<Project[]>(`/clients/${client}/projects`).then(setProjects).catch((e) => setError(errMsg(e)));
  }, [client]);

  return (
    <Box sx={{ p: 4 }}>
      <PageHeader title={info?.name ?? client} subtitle="Projects (dashboards) for this client"
        action={
          <Box sx={{ display: "flex", gap: 1 }}>
            <CopyLinkButton label="Copy client link" url={publicClientUrl(client)} />
            <ClientPreviewButton to={`${PREVIEW_BASE}/c/${client}`} />
            {tab === "projects" && <Btn onClick={() => setAdding(true)}>Create project</Btn>}
          </Box>
        } />
      <Tabs value={tab} onChange={(_, v: string) => setParams(v === "settings" ? { tab: "settings" } : {})} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
        <Tab value="projects" label="Projects" />
        <Tab value="settings" label="Settings" />
      </Tabs>
      {adding && (
        <NameSlugForm label="Project" onCancel={() => setAdding(false)}
          onSubmit={async (name, slug) => { await post(`/clients/${client}/projects`, { name, slug }); nav(`/c/${client}/p/${slug}`); }} />
      )}
      {tab === "settings" && <ClientSettings client={client} clientName={info?.name ?? client} />}
      {tab === "projects" && (
        <>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Client portal: <Box component="span" sx={monoSx}>{publicClientUrl(client)}</Box>
      </Typography>
      <ErrorBox error={error} />
      {projects === null && !error && <Loading />}
      {projects?.length === 0 && <Typography color="text.secondary">No projects yet.</Typography>}
      <Box sx={gridSx}>
        {projects?.map((p) => <LinkCard key={p.slug} to={`/c/${client}/p/${p.slug}`} title={p.name} subtitle={`${publicClientUrl(client).replace("https://", "")}/${p.slug}`} />)}
      </Box>
        </>
      )}
    </Box>
  );
}

export { NameSlugForm };
