import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, post, type Project, type ViewSummary } from "../api";
import { Btn, Card, ErrorBox, errMsg, viewStatus } from "../ui";
import { NameSlugForm } from "./Clients";
import { Settings } from "./Settings";

export function ProjectPage() {
  const { client = "", project = "" } = useParams();
  const [info, setInfo] = useState<Project | null>(null);
  const [tab, setTab] = useState<"settings" | "builder" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Project>(`/clients/${client}/projects/${project}`)
      .then((p) => { setInfo(p); setTab((t) => t ?? (p.config?.tables.length ? "builder" : "settings")); })
      .catch((e) => setError(errMsg(e)));
  }, [client, project]);

  const tabBtn = (id: "settings" | "builder", label: string) => (
    <button onClick={() => setTab(id)}
      className={`border-b-2 px-4 py-2 text-sm font-medium ${tab === id ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
      {label}
    </button>
  );

  return (
    <div className="p-8">
      <h1 className="text-xl font-semibold">{info?.name ?? project}</h1>
      <div className="mb-4 font-mono text-xs text-slate-500">{client}.radar-analytics.com/{project}</div>
      <ErrorBox error={error} />
      <div className="mb-6 flex border-b border-slate-200">{tabBtn("settings", "Settings")}{tabBtn("builder", "Builder")}</div>
      {tab === "settings" && (
        <Settings client={client} project={project} initial={info?.config ?? null}
          onSaved={(cfg) => setInfo((p) => (p ? { ...p, config: cfg } : p))} />
      )}
      {tab === "builder" && <ViewList client={client} project={project} hasTables={!!info?.config?.tables.length} goSettings={() => setTab("settings")} />}
    </div>
  );
}

function ViewList({ client, project, hasTables, goSettings }: { client: string; project: string; hasTables: boolean; goSettings: () => void }) {
  const [views, setViews] = useState<ViewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    get<ViewSummary[]>(`/clients/${client}/projects/${project}/views`).then(setViews).catch((e) => setError(errMsg(e)));
  }, [client, project]);

  return (
    <div className="space-y-4">
      {!hasTables && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          No BigQuery tables are connected yet, so queries will be rejected.{" "}
          <button className="font-medium underline" onClick={goSettings}>Open Settings</button>
        </div>
      )}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Views</h2>
        {!adding && <Btn onClick={() => setAdding(true)}>Add view</Btn>}
      </div>
      {adding && (
        <NameSlugForm label="View" onCancel={() => setAdding(false)}
          onSubmit={async (name, slug) => { await post(`/clients/${client}/projects/${project}/views`, { name, slug }); nav(`/c/${client}/p/${project}/v/${slug}`); }} />
      )}
      <ErrorBox error={error} />
      {views?.length === 0 && <div className="text-sm text-slate-500">No views yet.</div>}
      <div className="space-y-2">
        {views?.map((v) => (
          <Link key={v.slug} to={`/c/${client}/p/${project}/v/${v.slug}`}>
            <Card className="mb-2 flex items-center justify-between hover:border-blue-400">
              <div>
                <div className="font-medium">{v.name}</div>
                <div className="font-mono text-xs text-slate-500">{v.slug}</div>
              </div>
              {viewStatus(v)}
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
