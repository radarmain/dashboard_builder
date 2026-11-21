import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, post, type Client, type Project } from "../api";
import { Btn, Card, ErrorBox, Field, SLUG_RE, errMsg, inputCls, slugify } from "../ui";

/** Name + auto-suggested, editable, validated slug. */
function NameSlugForm({ label, onSubmit, onCancel }: {
  label: string; onSubmit: (name: string, slug: string) => Promise<void>; onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slugOk = SLUG_RE.test(slug) && slug.length <= 63;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await onSubmit(name.trim(), slug); } catch (err) { setError(errMsg(err)); setBusy(false); }
  }
  return (
    <Card className="max-w-lg">
      <form onSubmit={submit} className="space-y-3">
        <Field label={`${label} name`}>
          <input className={inputCls} autoFocus value={name}
            onChange={(e) => { setName(e.target.value); if (!touched) setSlug(slugify(e.target.value)); }} />
        </Field>
        <Field label="Slug" hint="Lowercase letters, numbers and hyphens. Cannot be changed later.">
          <input className={`${inputCls} font-mono`} value={slug}
            onChange={(e) => { setTouched(true); setSlug(e.target.value); }} />
        </Field>
        {slug && !slugOk && <div className="text-xs text-red-600">Slug must match ^[a-z0-9]+(-[a-z0-9]+)*$</div>}
        <ErrorBox error={error} />
        <div className="flex gap-2">
          <Btn type="submit" disabled={busy || !name.trim() || !slugOk}>{busy ? "Creating…" : "Create"}</Btn>
          <Btn type="button" variant="ghost" onClick={onCancel}>Cancel</Btn>
        </div>
      </form>
    </Card>
  );
}

export function ClientsPage() {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    get<Client[]>("/clients").then(setClients).catch((e) => setError(errMsg(e)));
  }, []);

  return (
    <div className="space-y-4 p-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Clients</h1>
        {!adding && <Btn onClick={() => setAdding(true)}>Add client</Btn>}
      </div>
      {adding && (
        <NameSlugForm label="Client" onCancel={() => setAdding(false)}
          onSubmit={async (name, slug) => { await post("/clients", { name, slug }); nav(`/c/${slug}`); }} />
      )}
      <ErrorBox error={error} />
      {clients === null && !error && <div className="text-sm text-slate-500">Loading…</div>}
      {clients?.length === 0 && <div className="text-sm text-slate-500">No clients yet.</div>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {clients?.map((c) => (
          <Link key={c.slug} to={`/c/${c.slug}`}>
            <Card className="hover:border-blue-400">
              <div className="font-medium">{c.name}</div>
              <div className="font-mono text-xs text-slate-500">{c.slug}</div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

export function ClientPage() {
  const { client = "" } = useParams();
  const [info, setInfo] = useState<Client | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    get<Client>(`/clients/${client}`).then(setInfo).catch((e) => setError(errMsg(e)));
    get<Project[]>(`/clients/${client}/projects`).then(setProjects).catch((e) => setError(errMsg(e)));
  }, [client]);

  return (
    <div className="space-y-4 p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{info?.name ?? client}</h1>
          <div className="text-sm text-slate-500">Projects (dashboards) for this client</div>
        </div>
        {!adding && <Btn onClick={() => setAdding(true)}>Create project</Btn>}
      </div>
      {adding && (
        <NameSlugForm label="Project" onCancel={() => setAdding(false)}
          onSubmit={async (name, slug) => { await post(`/clients/${client}/projects`, { name, slug }); nav(`/c/${client}/p/${slug}`); }} />
      )}
      <ErrorBox error={error} />
      {projects?.length === 0 && <div className="text-sm text-slate-500">No projects yet.</div>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {projects?.map((p) => (
          <Link key={p.slug} to={`/c/${client}/p/${p.slug}`}>
            <Card className="hover:border-blue-400">
              <div className="font-medium">{p.name}</div>
              <div className="font-mono text-xs text-slate-500">{client}.radar-analytics.com/{p.slug}</div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

export { NameSlugForm };
